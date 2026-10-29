"""
data.py - the insider data the pages show: trades grouped by company and by person, and the
lists and statistics of the Insiders pages (all markets, or one).
"""

from collections import Counter
from datetime import date, timedelta

from ..names import free_slug, slugify
from .markets import ALL_MARKETS, MARKETS, market_of
from .models import Company, Person
from .rules import combine_lots, mark_duplicates, mark_superseded, mark_transfers

LATEST = 50
BIGGEST = 20
ACTIVE = 20
ON_STOCK_PAGE = 50          # insider trades shown on a stock page before the "all" link


class InsiderData:
    def __init__(self, filings, trades, today):
        self.today = today
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
                if t.security and t.market.system.gives_isins:
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
        used = {m.slug for m in MARKETS.values()}             # /insiders/sweden/ is a market page
        for person in sorted(self.people.values(), key=lambda p: (p.name, p.cik)):
            person.slug = free_slug(slugify(person.name) or "insider", used)

    @property
    def markets(self):
        """The markets with data, in a fixed order."""
        present = {t.country for t in self.trades}
        return [MARKETS[code] for code in MARKETS if code in present]

    def market_info(self, country=None):
        return market_of(country) if country else ALL_MARKETS

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
            "currency": self.market_info(country).currency,
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
