"""
loader.py - reads the returns the returns collector worked out (table trade_returns): for every
stock trade, the stock's return 30, 182 and 365 days after the trade date and the return of SPY
(an S&P 500 fund) over the same days. Prices are never stored, only the returns.
"""

from ..loading import fetch_text, parse_date, parse_float, table_exists
from .models import Perf

RETURNS_SQL = """
select stock_key, to_char(trade_date, 'YYYY-MM-DD'), coalesce(security, ''), r30::text, r182::text, r365::text,
       m30::text, m182::text, m365::text, coalesce(note, '')
from trade_returns
"""


def load(db):
    """{(stock_key, trade date): Perf} for the trades that have at least one period worked out."""
    if not table_exists(db, "trade_returns"):
        return {}
    perfs = {}
    for key, day, security, r30, r182, r365, m30, m182, m365, note in fetch_text(db, RETURNS_SQL):
        stock = {30: parse_float(r30), 182: parse_float(r182), 365: parse_float(r365)}
        if all(value is None for value in stock.values()):
            continue
        perfs[(key, parse_date(day))] = Perf(security, stock, {30: parse_float(m30), 182: parse_float(m182), 365: parse_float(m365)},
                                             note)
    return perfs
