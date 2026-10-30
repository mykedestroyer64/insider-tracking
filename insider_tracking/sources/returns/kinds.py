"""
kinds.py - the kinds of trade that get returns, each with its own way of naming its stock in
the trade_returns table and of finding it in Sharadar's list of securities:

  congress   a ticker in a Congress report: T:<ticker>, found by the ticker on the trade day
  insider    a company in an SEC Form 4: C:<CIK>, found by the company's SEC number (the
             filing's ticker helps choose between its share classes)

The database lists each kind's trades (db/returns.py, one query per kind, by the same name), and
the website names a trade's stock with the same key to find its returns (website/returns/rules.py,
which also decides which of the site's trades are of the kind). A new kind of trade is one more
ReturnKind here, its query there and its rule on the website; the returns run doesn't change.
"""

from dataclasses import dataclass
from typing import Callable

from .keys import company_key, ticker_key

# The kinds of asset in Congress reports that get returns (the same list as the SQL in
# db/returns.py that picks the trades)
CONGRESS_TYPES = ("Stock", "Exchange Traded Fund", "Exchange Traded Note", "REIT")


@dataclass(frozen=True)
class ReturnKind:
    name: str
    key: Callable[[str], str]     # the stock's id -> its name in trade_returns
    find: Callable                # (universe, the stock's id, trade day, ticker hint) ->
                                  # (security, None), or (None, why there is none)


KINDS = {kind.name: kind for kind in (
    ReturnKind("congress", key=ticker_key,
               find=lambda universe, ticker, day, hint: universe.for_ticker(ticker, day)),
    ReturnKind("insider", key=company_key,
               find=lambda universe, cik, day, ticker: universe.for_company(cik, day, ticker)),
)}


def kind_of(name):
    """The ReturnKind called `name`; a clear error for a kind nobody has described."""
    try:
        return KINDS[name]
    except KeyError:
        raise KeyError(f"trades of kind {name!r} need returns, but sources/returns/kinds.py doesn't describe "
                       "that kind: add a ReturnKind for it") from None


def stock_key(kind, ident):
    """How a trade's stock is named in the trade_returns table (the site uses the same names)."""
    return kind_of(kind).key(ident)
