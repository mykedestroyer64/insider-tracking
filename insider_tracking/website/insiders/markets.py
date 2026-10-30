"""
markets.py - the countries whose insiders the site shows, and how their filings work.

A FilingSystem says what a country's filings mean for counting (the SEC's Form 4, or a
register under the EU Market Abuse Regulation); a Market is one country: its words on the
pages, its currency, its filing system, and the source's own rules for names and links.

A new country is one more Market in MARKETS (and its collector): the pages and the rules read
everything from here, so nothing else changes.
"""

from dataclasses import dataclass
from typing import Callable

from ...sources.sweden.names import company_short_name
from ...sources.sweden.site import register_search_url


@dataclass(frozen=True)
class FilingSystem:
    name: str
    corrections_replace: bool   # a corrected filing replaces the original (counted; the original isn't).
                                # Otherwise an amendment repeats a filing and isn't counted.
    joint_filers: bool          # each joint filer files the same trade: it is counted once
    transfers: bool             # a move between an insider and a related party comes as a buy and a sell
    gives_isins: bool           # each line names the instrument's ISIN
    gives_tickers: bool         # each filing names the stock's ticker (a register's "ticker" is the short name)
    filing_label: str           # how one filing is called ("{form}" is its form)


FORM_4 = FilingSystem("SEC Form 4", corrections_replace=False, joint_filers=True, transfers=False,
                      gives_isins=False, gives_tickers=True, filing_label="Form {form}")
MAR_REGISTER = FilingSystem("a register under the EU Market Abuse Regulation (Article 19)", corrections_replace=True,
                            joint_filers=False, transfers=True, gives_isins=True, gives_tickers=False,
                            filing_label="Register entry")


def same_name(name):
    """A company's short name when its source has no rule for one: the name itself."""
    return name


def no_link(person, issuer, day):
    """A link to a filing when its source can't link one: none."""
    return ""


@dataclass(frozen=True)
class Market:
    code: str                   # the country code the collectors store ("US", "SE")
    slug: str                   # /insiders/<slug>/
    name: str
    currency: str               # the currency its trades are in, unless a row says otherwise
    regulator: str = ""
    source: str = ""
    since: str = ""
    rule: str = ""              # who must report what, for the market's page
    coverage: str = ""          # what the site has, for the all-markets page
    insiders_note: str = ""     # a stock page's line about its insiders' trades ("{company}": its name)
    region: str = ""            # where it is, for the all-markets page ("the United States", "Europe")
    timezone: str = ""          # the time zone of its register's publication times (the filing day is
                                # the day there); "" when its filings are dated by day
    system: FilingSystem | None = None
    home: bool = False          # the site's home market: members of Congress trade its stocks, and its
                                # companies need no country after their name
    returns: bool = False       # the returns collector has prices for its stocks
    short_name: Callable[[str], str] = same_name        # the company's short name (registers give no ticker)
    entry_url: Callable[..., str] = no_link             # (person, issuer, trade day) -> a link to the register


MARKETS = {market.code: market for market in (
    Market(code="US", slug="united-states", name="United States", currency="USD",
           regulator="the SEC", source="SEC Form 4", since="2021",
           rule="Officers and directors must report trades in their own company’s stock to the SEC within two "
                "business days, on Form 4.",
           coverage="open-market buys and sells by officers and directors of US-listed companies since 2021",
           insiders_note="Open-market buys and sells by {company}’s own officers and directors, reported to the "
                         "SEC on Form 4 (since 2021).",
           region="the United States", system=FORM_4, home=True, returns=True),
    Market(code="SE", slug="sweden", name="Sweden", currency="SEK",
           regulator="Finansinspektionen", source="Finansinspektionen’s register", since="July 2016",
           rule="Executives and board members of companies listed in Sweden must report trades in their company’s "
                "shares to Finansinspektionen, the financial regulator, within three business days (EU Market "
                "Abuse Regulation, Article 19).",
           coverage="buys and sells of shares by executives and board members of companies listed in Sweden "
                    "since July 2016",
           insiders_note="Buys and sells of {company} shares by its own executives and board members, reported "
                         "to Finansinspektionen (since July 2016).",
           region="Europe", timezone="Europe/Stockholm",
           system=MAR_REGISTER, short_name=company_short_name, entry_url=register_search_url),
)}
ALL_MARKETS = Market(code="ALL", slug="", name="All markets", currency="EUR")


def regions():
    """Where the markets are, in words: 'the United States and Europe'."""
    names = list(dict.fromkeys(market.region for market in MARKETS.values()))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def published_day_sql(column):
    """SQL: the day a register published a filing (`column`, a timestamp), in its country's time zone."""
    zones = " ".join(f"when '{market.code}' then '{market.timezone}'" for market in MARKETS.values()
                     if market.timezone)
    return f"to_char({column} at time zone (case country {zones} else 'UTC' end), 'YYYY-MM-DD')"


def market_of(country):
    """The Market of a country code; a clear error for a country nobody has described yet."""
    try:
        return MARKETS[country]
    except KeyError:
        raise KeyError(f"insider trades from country {country!r}, which website/insiders/markets.py doesn't "
                       "describe yet: add a Market for it") from None
