"""
models.py - what the Insiders pages are built from.

Two sources feed the Insiders section: the SEC's Form 4 filings (US officers and directors)
and European registers of executives' trades (EU Market Abuse Regulation, Article 19; Sweden
first). Both become the same objects: a Filing (one report) with InsiderTrades, grouped by
Company (a stock page) and by Person (an insider page). Each trade knows its market, so the
pages can show one market or all of them.

Amounts stay in the currency of the trade; a euro figure (ECB reference rate of the trade
day) sits next to anything that isn't dollars or euros, and ranks trades across markets.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from ..names import stock_slug
from ..formatting import shows_eur
from .markets import market_of

VENUES = {   # the register's venue names, shortened
    "nasdaq stockholm ab": "Nasdaq Stockholm", "omx nordic exchange stockholm ab": "Nasdaq Stockholm",
    "nasdaq - all markets": "Nasdaq", "first north sweden": "First North", "first north sweden - sme growth market": "First North",
    "nordic growth market": "NGM", "spotlight stock market": "Spotlight", "outside a trading venue": "off-exchange",
}


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
        return market_of(self.country)

    @property
    def source_label(self):
        return self.market.system.filing_label.format(form=self.form)


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
    def market(self):
        return self.filing.market

    @property
    def repeats_a_filing(self):
        """An amendment that only repeats a filing (Form 4/A), so it isn't counted."""
        return self.filing.is_amendment and not self.market.system.corrections_replace

    @property
    def counted(self):
        if self.suspect or self.duplicate or self.superseded or self.programme or self.transfer:
            return False
        if self.status.lower() != "current":
            return False
        if self.repeats_a_filing:
            return False
        return True

    @property
    def label(self):
        return "Buy" if self.kind == "buy" else "Sell"

    @property
    def note(self):
        """The one line under the Buy/Sell label that says why a row isn't counted, if it isn't."""
        if self.repeats_a_filing:
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
        if self.filing.is_amendment and self.market.system.corrections_replace:
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
        return shows_eur(self.currency) and self.value_eur is not None


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
        return market_of(self.country)

    @property
    def currency(self):
        return self.market.currency

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
        return market_of(self.country)

    @property
    def currency(self):
        return self.market.currency

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
                                      "title": t.role_text, "country": t.country, "market": t.market}
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
            "amendments": sum(1 for t in self.trades if t.repeats_a_filing),
            "suspect": sum(1 for t in self.trades if t.suspect),
            "not_current": sum(1 for t in self.trades if t.status.lower() != "current" or t.superseded),
            "programme": sum(1 for t in self.trades if t.programme),
            "transfers": sum(1 for t in self.trades if t.transfer),
        }
