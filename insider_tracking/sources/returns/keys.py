"""
keys.py - how a trade's stock is named in the trade_returns table (the website uses the same
names to find a trade's returns): T:<ticker> for a ticker, C:<CIK> for a company's SEC number.
"""

import re


def normalise_ticker(ticker):
    """'BRK/B', 'BRK-B' and 'brk.b' -> 'BRK.B' (Sharadar's spelling)."""
    return re.sub(r"[/\-]", ".", (ticker or "").strip().upper())


def ticker_key(ticker):
    """T:<ticker>: a stock named by its ticker (a Congress report)."""
    return "T:" + normalise_ticker(ticker)


def company_key(cik):
    """C:<CIK>: a company named by its SEC number, without leading zeros (an SEC Form 4)."""
    return "C:" + (str(cik or "").lstrip("0") or "0")
