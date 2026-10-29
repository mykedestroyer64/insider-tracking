"""
rules.py - which trades get a return, and how a person's trades did:

  - after a buy, a stock that did better than the S&P 500 "beat" it;
  - after a sell, a stock that did worse than the S&P 500 made the sale well timed.

Every trade counts the same, whatever its size (Congress reports give only ranges).
"""

from ...sources.returns.keys import CONGRESS_TYPES, stock_key
from .models import PERIODS


def congress_key(trade):
    """The trade_returns key of a Congress trade, or None if it gets no return (options, bonds, exchanges)."""
    if len(trade.tickers) != 1 or trade.group not in ("buy", "sell") or not trade.traded_ok:
        return None
    if (trade.asset_type or "Stock") not in CONGRESS_TYPES or trade.is_amendment:
        return None
    return stock_key("congress", trade.tickers[0]), trade.traded_ok


def insider_key(trade):
    """The trade_returns key of a US insider trade, or None (European trades get no return)."""
    if trade.country != "US" or not trade.filing.company_cik or not trade.traded:
        return None
    return stock_key("insider", trade.filing.company_cik), trade.traded


def attach(perfs, congress_trades, insider_trades):
    """Give every trade a .perf (a Perf, or None). Returns how many trades got one."""
    found = 0
    for trades, key_of in ((congress_trades, congress_key), (insider_trades, insider_key)):
        for trade in trades:
            key = key_of(trade)
            trade.perf = perfs.get(key) if key else None
            found += trade.perf is not None
    return found


def summary(trades, kind_of):
    """Per side (buy / sell) and period: how many trades, the average returns, and how often the
    trade came out right against the S&P 500 (a buy beat it, or a sale was followed by worse)."""
    table = {}
    for side in ("buy", "sell"):
        for period in PERIODS:
            pairs = [(t.perf.stock[period], t.perf.market[period]) for t in trades
                     if getattr(t, "perf", None) and kind_of(t) == side and t.perf.has(period)]
            if not pairs:
                continue
            count = len(pairs)
            right = sum(1 for stock, market in pairs if (stock > market if side == "buy" else stock < market))
            table[(side, period)] = {
                "count": count,
                "stock": sum(stock for stock, _ in pairs) / count,
                "market": sum(market for _, market in pairs) / count,
                "right": right / count,
            }
    return table
