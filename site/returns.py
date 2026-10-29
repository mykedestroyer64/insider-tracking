"""
returns.py - how each trade's stock did afterwards, for the website.

The returns collector (collect_returns.py) stores, for every stock trade, the stock's
return 30, 182 and 365 days after the trade date and the return of SPY (an S&P 500
fund) over the same days, worked out from Sharadar's prices. This module reads them,
attaches them to the trades, and sums them up per person:

  - after a buy, a stock that did better than the S&P 500 "beat" it;
  - after a sell, a stock that did worse than the S&P 500 made the sale well timed.

Every trade counts the same, whatever its size (Congress reports give only ranges).
"""

from dataclasses import dataclass, field

from data import fetch_all, parse_date

PERIODS = (30, 182, 365)
LABELS = {30: "1 month", 182: "6 months", 365: "1 year"}
SHORT = {30: "1 mo", 182: "6 mo", 365: "1 yr"}

RETURNS_SQL = """
select stock_key, to_char(trade_date, 'YYYY-MM-DD'), coalesce(security, ''), r30::text, r182::text, r365::text,
       m30::text, m182::text, m365::text, coalesce(note, '')
from trade_returns
"""
HAS_TABLE_SQL = "select count(*)::text from information_schema.tables where table_name = 'trade_returns'"

CONGRESS_TYPES = ("Stock", "Exchange Traded Fund", "Exchange Traded Note", "REIT")


def _number(text):
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


@dataclass
class Perf:
    security: str
    stock: dict = field(default_factory=dict)      # {30: 0.021, 182: None, 365: None}
    market: dict = field(default_factory=dict)
    note: str = ""

    def has(self, period):
        return self.stock.get(period) is not None and self.market.get(period) is not None

    @property
    def best(self):
        """The longest period that has ended: (period, stock return, S&P 500 return), or None."""
        for period in reversed(PERIODS):
            if self.has(period):
                return period, self.stock[period], self.market[period]
        return None

    @property
    def stopped(self):
        return self.note.startswith("stopped")


def load(conn):
    """{(stock_key, trade date): Perf} for the trades that have at least one period worked out."""
    if fetch_all(conn, HAS_TABLE_SQL)[0][0] == "0":
        return {}
    perfs = {}
    for key, day, security, r30, r182, r365, m30, m182, m365, note in fetch_all(conn, RETURNS_SQL):
        stock = {30: _number(r30), 182: _number(r182), 365: _number(r365)}
        if all(value is None for value in stock.values()):
            continue
        perfs[(key, parse_date(day))] = Perf(security, stock, {30: _number(m30), 182: _number(m182), 365: _number(m365)},
                                             note)
    return perfs


def congress_key(trade):
    """The trade_returns key of a Congress trade, or None if it gets no return (options, bonds, exchanges)."""
    import returns_calc   # the collector's own rules (the repository root is on the path)
    if len(trade.tickers) != 1 or trade.group not in ("buy", "sell") or not trade.traded_ok:
        return None
    if (trade.asset_type or "Stock") not in CONGRESS_TYPES or trade.is_amendment:
        return None
    return returns_calc.stock_key("congress", trade.tickers[0]), trade.traded_ok


def insider_key(trade):
    import returns_calc
    if trade.country != "US" or not trade.filing.company_cik or not trade.traded:
        return None
    return returns_calc.stock_key("insider", trade.filing.company_cik), trade.traded


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


def fmt_pct(value, signed=True):
    """+12.4% / −3.1% (a real minus sign), or – when unknown."""
    if value is None:
        return "–"
    percent = value * 100
    if signed and round(percent, 1) > 0:
        return f"+{percent:,.1f}%"
    if round(percent, 1) < 0:
        return f"−{abs(percent):,.1f}%"
    return "0.0%"
