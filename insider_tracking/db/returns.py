"""
returns.py - how each traded stock did afterwards (table trade_returns), and which trades need it.

Only returns are stored, never prices (Sharadar's licence). A row is keyed by the stock
(T:<ticker> for Congress, C:<CIK> for an SEC filer) and the trade date.
"""

from datetime import date

from .database import chunks, row_slots

# The trades that get returns, one query per kind of trade (sources/returns/kinds.py names the
# kinds), each giving (the stock's id, the trade day, a ticker that helps find it, or null):
#   congress: single-ticker stock trades in Congress reports (not amendments, not dated after the report)
#   insider:  company insiders' buys and sells (not suspect, not amendments)
CONGRESS_RETURN_ITEMS_SQL = """
    select distinct trim(t.ticker), t.transaction_date, null
    from transactions t join reports r on r.report_path = t.report_path
    where t.transaction_date is not null and t.transaction_date >= '2012-01-01'
      and t.transaction_date <= coalesce(r.date_received, t.transaction_date)
      and t.ticker is not null and position(' ' in trim(t.ticker)) = 0
      and trim(t.ticker) not in ('', '--', 'N/A')
      and coalesce(nullif(t.asset_type, ''), 'Stock') in ('Stock', 'Exchange Traded Fund', 'Exchange Traded Note', 'REIT')
      and t.trade_type in ('purchase', 'sale_full', 'sale_partial', 'sale')
      and not r.is_amendment"""

INSIDER_RETURN_ITEMS_SQL = """
    select f.company_cik, t.trade_date, max(f.ticker)
    from insider_trades t join insider_filings f on f.accession = t.accession
    where t.trade_date is not null and not t.suspect and not f.is_amendment and f.company_cik is not null
    group by f.company_cik, t.trade_date"""
ITEM_QUERIES = {"congress": CONGRESS_RETURN_ITEMS_SQL, "insider": INSIDER_RETURN_ITEMS_SQL}

RETURN_COLUMNS = ["stock_key", "trade_date", "security", "r30", "r182", "r365", "m30", "m182", "m365",
                  "note", "checked_on"]


def as_date(value):
    if value is None or hasattr(value, "year"):
        return value
    return date.fromisoformat(str(value)[:10])


def as_float(value):
    return None if value is None else float(value)


class TradeReturns:
    def __init__(self, db):
        self.db = db

    def items(self):
        """The trades that get returns, as (kind, the stock's id, trade day, ticker hint or None):
        [('congress', ticker, day, None), ('insider', cik, day, ticker), ...]."""
        items = []
        with self.db.transaction() as cursor:
            for kind, sql in ITEM_QUERIES.items():
                cursor.execute(sql)
                items += [(kind, str(ident), as_date(day), hint) for ident, day, hint in cursor.fetchall()]
        return items

    def stored(self):
        """{(stock_key, trade_date): (r30, r182, r365, note, checked_on)} for every stored row."""
        with self.db.transaction() as cursor:
            cursor.execute("select stock_key, trade_date, r30, r182, r365, note, checked_on from trade_returns")
            return {(str(key), as_date(day)): (as_float(r30), as_float(r182), as_float(r365), note, as_date(checked))
                    for key, day, r30, r182, r365, note, checked in cursor.fetchall()}

    def save(self, rows):
        """Store worked-out returns (a row seen before is replaced). rows: dicts with RETURN_COLUMNS."""
        if not rows:
            return 0
        updates = ", ".join(f"{c} = excluded.{c}" for c in RETURN_COLUMNS[2:])
        with self.db.transaction() as cursor:
            for chunk in chunks(rows, 500):
                params = []
                for row in chunk:
                    params += [row.get(column) for column in RETURN_COLUMNS]
                cursor.execute(f"insert into trade_returns ({', '.join(RETURN_COLUMNS)}) values "
                               + ", ".join([row_slots(len(RETURN_COLUMNS))] * len(chunk))
                               + f" on conflict (stock_key, trade_date) do update set {updates}", params)
        return len(rows)

    def counts(self):
        with self.db.transaction() as cursor:
            cursor.execute("""
        select count(*), count(r30), count(r182), count(r365), count(*) filter (where left(note, 7) = 'stopped'),
               count(*) filter (where r30 is null and note is not null and left(note, 7) <> 'stopped'),
               pg_size_pretty(pg_total_relation_size('trade_returns'))
        from trade_returns""")
            rows, r30, r182, r365, stopped, failed, size = cursor.fetchone()
        return {"rows": int(rows), "r30": int(r30), "r182": int(r182), "r365": int(r365),
                "stopped": int(stopped), "failed": int(failed), "size": str(size)}
