"""
loader.py - reads the company-insider tables for the Insiders pages: the SEC tables
(insider_filings, insider_trades, keyed by the SEC's CIK numbers) and the European registers'
table (mar_trades, one row per transaction line, keyed by the country and the person's name).
"""

from datetime import timedelta

from ...sources.sec.names import accession_url, company_display_name, is_entity
from ..loading import fetch_text, parse_date, parse_float, table_exists
from ..names import free_slug, slugify
from .data import InsiderData
from .markets import market_of, published_day_sql
from .models import Filing, InsiderTrade, person_key, venue_text

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

MAR_SQL = f"""
select country, row_key, filing_key, {published_day_sql("published_at")},
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


class UsdRates:
    """Euro value of dollar trades, from the ECB's rates (1 EUR = rate USD), nearest earlier day."""

    def __init__(self, rows):
        self.rates = {parse_date(day): parse_float(rate) for day, _, rate in rows}
        self.rates.pop(None, None)

    def eur(self, value, day):
        if value is None or day is None or not self.rates:
            return None
        for back in range(11):
            rate = self.rates.get(day - timedelta(days=back))
            if rate:
                return value / rate
        return None


def load_us(db, rates):
    """The SEC tables -> (filings, trades)."""
    if not table_exists(db, "insider_trades"):
        return [], []
    filings = {}
    for row in fetch_text(db, FILINGS_SQL):
        filings[row[0]] = Filing(
            accession=row[0], form=row[1], filed=parse_date(row[2]), period=parse_date(row[3]),
            company_cik=row[4], company_name=row[5], ticker=row[6].upper(),
            plan=None if row[7] == "" else row[7] == "1", is_amendment=row[8] == "1",
            url=accession_url(row[0], row[4]) or "",
        )
    for filing in filings.values():
        filing.company_name = company_display_name(filing.company_name) or filing.company_name
    trades = []
    for row in fetch_text(db, TRADES_SQL):
        filing = filings.get(row[0])
        if filing is None or is_entity(row[3]):
            continue
        traded = parse_date(row[6])
        value = parse_float(row[10])
        trades.append(InsiderTrade(
            filing=filing, row_key=row[1], insider_cik=row[2], insider_name=row[3], insider_title=row[4],
            roles=[r for r in (row[5] or "").split("|") if r], traded=traded, kind=row[7],
            shares=parse_float(row[8]), price=parse_float(row[9]), value=value, direct=row[11] == "1",
            nature=row[12], security=row[13], suspect=row[14] == "1", suspect_reason=row[15],
            currency="USD", value_eur=rates.eur(value, traded),
        ))
    return list(filings.values()), trades


def load_europe(db):
    """The mar_trades table -> (filings, trades): one Filing per notification (filing_key)."""
    if not table_exists(db, "mar_trades"):
        return [], []
    rows = fetch_text(db, MAR_SQL)
    # company slugs first: /stocks/se/<short name>/, unique per issuer (LEI, or name)
    issuers = {}
    for row in rows:
        country, issuer_name, lei = row[0], row[4], row[6]
        key = (country, lei or issuer_name.lower())
        issuers.setdefault(key, {"name": issuer_name, "short": market_of(country).short_name(issuer_name) or issuer_name,
                                 "slug": None})
    used = set()
    for key in sorted(issuers, key=lambda k: issuers[k]["short"].lower()):
        info = issuers[key]
        info["slug"] = free_slug(f"{key[0].lower()}/{slugify(info['short'])}", used)
    filings, trades = {}, []
    for row in rows:
        (country, row_key, filing_key, published, issuer_name, issuer_short, lei, notifier, person, position, role,
         closely, amendment, programme, instrument_type, instrument_name, isin, traded, kind, volume, price,
         currency, value, value_eur, venue, status, suspect, reason) = row
        issuer = issuers[(country, lei or issuer_name.lower())]
        market = market_of(country)
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
            filing.url = market.entry_url(person, issuer_name, trade_day)
        trades.append(InsiderTrade(
            filing=filing, row_key=row_key, insider_cik=person_key(country, person), insider_name=person,
            insider_title=position, roles=[role] if role else [], traded=trade_day, kind=kind,
            shares=parse_float(volume), price=parse_float(price), value=parse_float(value), direct=closely != "1",
            nature=(f"via {notifier}" if notifier else "closely associated person") if closely == "1" else "",
            security=isin, suspect=suspect == "1", suspect_reason=reason, currency=currency or market.currency,
            value_eur=parse_float(value_eur), status=status or "Current", programme=programme == "1",
            venue=venue_text(venue), instrument=instrument_name,
        ))
    return list(filings.values()), trades


def load(db, today):
    """Read every insider source. Returns an InsiderData (empty when no table exists yet)."""
    rates = UsdRates(fetch_text(db, FX_SQL) if table_exists(db, "fx_rates") else [])
    us_filings, us_trades = load_us(db, rates)
    eu_filings, eu_trades = load_europe(db)
    return InsiderData(us_filings + eu_filings, us_trades + eu_trades, today)
