"""
europe.py - European executives' buys and sells (table mar_trades), one row per transaction
line of a register, keyed by country and the line's own key. A line seen before is updated
(its status may have become Revised or Cancelled), never duplicated.
"""

from .database import chunks, is_true, row_slots

MAR_COLUMNS = ["country", "row_key", "filing_key", "published_at", "issuer_name", "issuer_short", "issuer_lei",
               "notifier", "person_name", "position", "role", "position_raw", "closely_associated", "is_amendment",
               "amendment_note", "initial", "option_programme", "nature", "kind", "instrument_type",
               "instrument_name", "isin", "trade_date", "volume", "unit", "price", "currency", "value", "value_eur",
               "venue", "status", "suspect", "suspect_reason", "source"]
MAR_UPDATED = ["status", "amendment_note", "value_eur", "suspect", "suspect_reason", "source", "issuer_short",
               "position", "role", "notifier", "person_name", "issuer_name"]


class MarTrades:
    def __init__(self, db):
        self.db = db

    def save(self, rows, source):
        """Store transaction lines from a register (a line seen before is updated).

        Returns (new rows, updated rows)."""
        if not rows:
            return 0, 0
        new = updated = 0
        updates = ", ".join(f"{c} = excluded.{c}" for c in MAR_UPDATED)
        with self.db.transaction() as cursor:
            for chunk in chunks(rows, 100):
                params = []
                for row in chunk:
                    values = dict(row, source=source)
                    params += [values.get(column) for column in MAR_COLUMNS]
                cursor.execute(
                    f"insert into mar_trades ({', '.join(MAR_COLUMNS)}) values "
                    + ", ".join([row_slots(len(MAR_COLUMNS))] * len(chunk))
                    + f" on conflict (country, row_key) do update set {updates}, last_seen_at = now() "
                    "returning (xmax = 0)", params)
                for (is_new,) in cursor.fetchall():
                    if is_true(is_new):
                        new += 1
                    else:
                        updated += 1
        return new, updated

    def without_eur(self, country):
        """Rows whose euro value could not be worked out yet (no exchange rate at the time)."""
        with self.db.transaction() as cursor:
            cursor.execute("select row_key, trade_date, currency, value from mar_trades "
                           "where country = %s and value is not null and value_eur is null and currency is not null",
                           (country,))
            rows = cursor.fetchall()
        return [{"row_key": str(r[0]), "trade_date": r[1], "currency": str(r[2]), "value": float(r[3])} for r in rows]

    def set_eur(self, country, values):
        """values: {row_key: value_eur}."""
        with self.db.transaction() as cursor:
            for row_key, value_eur in values.items():
                cursor.execute("update mar_trades set value_eur = %s where country = %s and row_key = %s",
                               (value_eur, country, row_key))

    def counts(self, country):
        with self.db.transaction() as cursor:
            cursor.execute("""
        select count(*), count(*) filter (where kind = 'buy'), count(*) filter (where kind = 'sell'),
               count(*) filter (where suspect), count(*) filter (where status <> 'Current'),
               count(distinct person_name), count(distinct coalesce(issuer_lei, issuer_name)),
               min(published_at), max(published_at)
        from mar_trades where country = %s""", (country,))
            trades, buys, sells, suspect, not_current, people, companies, first, last = cursor.fetchone()
            cursor.execute("select pg_size_pretty(pg_total_relation_size('mar_trades'))")
            size = cursor.fetchone()[0]
        return {"trades": int(trades), "buys": int(buys), "sells": int(sells), "suspect": int(suspect),
                "not_current": int(not_current), "people": int(people), "companies": int(companies),
                "first_published": first, "last_published": last, "size": str(size)}
