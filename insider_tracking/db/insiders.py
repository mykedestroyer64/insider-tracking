"""
insiders.py - SEC Form 4 filings and their buys and sells (tables insider_filings, insider_trades).

A filing seen before is replaced, never duplicated: the quarterly files and the daily lists
can hold the same filing.
"""

from .database import chunks, is_true, row_slots

FILING_COLUMNS = ["accession", "form", "filing_date", "period", "company_cik", "company_name", "ticker",
                  "plan_10b5_1", "is_amendment", "original_date", "source"]
INSIDER_TRADE_COLUMNS = ["accession", "row_key", "insider_cik", "insider_name", "insider_title", "roles",
                         "trade_date", "code", "kind", "shares", "price", "value", "acquired_disposed",
                         "shares_after", "direct", "nature", "security", "suspect", "suspect_reason"]


def postgres_pattern(pattern):
    """A Python regular expression as PostgreSQL writes it (its word boundary is \\y, not \\b)."""
    return pattern.pattern.replace("\\b", "\\y")


class InsiderFilings:
    def __init__(self, db):
        self.db = db

    def save(self, filings, trades, source):
        """Store filings and their buys and sells (a filing seen before is replaced).

        Returns how many filings were new."""
        if not filings:
            return 0
        new = 0
        by_filing = {}
        for trade in trades:
            by_filing.setdefault(trade["accession"], []).append(trade)
        with self.db.transaction() as cursor:
            for chunk in chunks(filings, 50):
                params = []
                for filing in chunk:
                    values = dict(filing, source=source)
                    params += [values[column] for column in FILING_COLUMNS]
                cursor.execute(
                    f"insert into insider_filings ({', '.join(FILING_COLUMNS)}) values "
                    + ", ".join([row_slots(len(FILING_COLUMNS))] * len(chunk))
                    + " on conflict (accession) do update set form = excluded.form, filing_date = excluded.filing_date, "
                    "period = excluded.period, company_cik = excluded.company_cik, company_name = excluded.company_name, "
                    "ticker = excluded.ticker, plan_10b5_1 = excluded.plan_10b5_1, is_amendment = excluded.is_amendment, "
                    "original_date = excluded.original_date, source = excluded.source returning (xmax = 0)", params)
                new += sum(1 for row in cursor.fetchall() if is_true(row[0]))
                accessions = [filing["accession"] for filing in chunk]
                cursor.execute("delete from insider_trades where accession = any(%s)", (accessions,))
                rows = [trade for filing in chunk for trade in by_filing.get(filing["accession"], [])]
                for part in chunks(rows, 100):
                    tparams = []
                    for trade in part:
                        tparams += [trade[column] for column in INSIDER_TRADE_COLUMNS]
                    cursor.execute(f"insert into insider_trades ({', '.join(INSIDER_TRADE_COLUMNS)}) values "
                                   + ", ".join([row_slots(len(INSIDER_TRADE_COLUMNS))] * len(part)), tparams)
        return new

    def remove_entities(self, pattern):
        """Delete trades attributed to funds or companies (saved before the rule that keeps people
        only): names that `pattern` finds (a Python regular expression, any case) and names that
        start with a digit. Returns how many trades went."""
        with self.db.transaction() as cursor:
            cursor.execute("delete from insider_trades where insider_name ~* %s or insider_name ~ '^\\d' returning 1",
                           (postgres_pattern(pattern),))
            removed = len(cursor.fetchall())
            cursor.execute("delete from insider_filings f where not exists "
                           "(select 1 from insider_trades t where t.accession = f.accession)")
        return removed or 0

    def counts(self):
        with self.db.transaction() as cursor:
            cursor.execute("select count(*), count(*) filter (where is_amendment), min(filing_date), max(filing_date) "
                           "from insider_filings")
            filings, amendments, first, last = cursor.fetchone()
            cursor.execute("select count(*), count(*) filter (where kind = 'buy'), count(*) filter (where kind = 'sell'), "
                           "count(*) filter (where suspect), count(distinct insider_cik), count(distinct accession) "
                           "from insider_trades")
            trades, buys, sells, suspect, insiders, _ = cursor.fetchone()
            cursor.execute("select pg_size_pretty(pg_total_relation_size('insider_trades') + "
                           "pg_total_relation_size('insider_filings'))")
            size = cursor.fetchone()[0]
        return {"filings": int(filings), "amendments": int(amendments), "first_filing": first, "last_filing": last,
                "trades": int(trades), "buys": int(buys), "sells": int(sells), "suspect": int(suspect),
                "insiders": int(insiders), "size": str(size)}
