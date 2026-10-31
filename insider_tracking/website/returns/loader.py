"""
loader.py - reads the returns the returns collector worked out (table trade_returns): for every
stock trade, the stock's return 30, 182 and 365 days after the trade date and the return of SPY
(an S&P 500 fund) over the same days. Prices are never stored, only the returns.
"""

from ..loading import parse_date, parse_float
from ..rows import SiteQuery
from .models import Perf

RETURNS = SiteQuery(
    name="trade_returns",
    columns="""stock_key, to_char(trade_date, 'YYYY-MM-DD'), coalesce(security, ''), r30::text, r182::text, r365::text,
       m30::text, m182::text, m365::text, coalesce(note, '')""",
    tables="trade_returns", order="trade_date desc, stock_key", part="to_char(trade_date, 'YYYY-MM-DD')",
    descending=True)


def load(source):
    """{(stock_key, trade date): Perf} for the trades that have at least one period worked out.
    source: a RowSource (website/rows.py)."""
    if not source.has_table("trade_returns"):
        return {}
    perfs = {}
    for key, day, security, r30, r182, r365, m30, m182, m365, note in source.rows(RETURNS):
        stock = {30: parse_float(r30), 182: parse_float(r182), 365: parse_float(r365)}
        if all(value is None for value in stock.values()):
            continue
        perfs[(key, parse_date(day))] = Perf(security, stock, {30: parse_float(m30), 182: parse_float(m182), 365: parse_float(m365)},
                                             note)
    return perfs
