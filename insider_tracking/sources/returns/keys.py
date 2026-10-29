"""
keys.py - how a trade's stock is named in the trade_returns table (the website uses the same
names to find a trade's returns).
"""

import re

# The kinds of asset in Congress reports that get returns (the same list as the SQL in
# db/returns.py that picks the trades)
CONGRESS_TYPES = ("Stock", "Exchange Traded Fund", "Exchange Traded Note", "REIT")


def normalise_ticker(ticker):
    """'BRK/B', 'BRK-B' and 'brk.b' -> 'BRK.B' (Sharadar's spelling)."""
    return re.sub(r"[/\-]", ".", (ticker or "").strip().upper())


def stock_key(source, ident):
    """How a trade's stock is named in the trade_returns table (the site uses the same names):
    T:<ticker> for a ticker in a Congress report, C:<CIK> for a company in an SEC Form 4."""
    if source == "congress":
        return "T:" + normalise_ticker(ident)
    return "C:" + (str(ident or "").lstrip("0") or "0")
