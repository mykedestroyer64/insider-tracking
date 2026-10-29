"""fx.py - the European Central Bank's daily euro exchange rates (table fx_rates: 1 EUR = rate units)."""

from datetime import date

from .database import chunks, is_true


class FxRates:
    def __init__(self, db):
        self.db = db

    def save(self, rows):
        """rows: [(day, currency, rate)]. Returns how many were new."""
        if not rows:
            return 0
        new = 0
        with self.db.transaction() as cursor:
            for chunk in chunks(rows, 500):
                params = []
                for day, currency, rate in chunk:
                    params += [day, currency, rate]
                cursor.execute("insert into fx_rates (day, currency, rate) values "
                               + ", ".join(["(%s, %s, %s)"] * len(chunk))
                               + " on conflict (day, currency) do update set rate = excluded.rate returning (xmax = 0)",
                               params)
                new += sum(1 for (is_new,) in cursor.fetchall() if is_true(is_new))
        return new

    def load(self):
        """All stored rates as [(day, currency, rate)]."""
        with self.db.transaction() as cursor:
            cursor.execute("select day, currency, rate from fx_rates")
            return [(r[0] if not isinstance(r[0], str) else date.fromisoformat(r[0]), str(r[1]), float(r[2]))
                    for r in cursor.fetchall()]
