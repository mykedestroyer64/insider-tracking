"""state.py - the collectors' bookmarks between runs (table collector_state: key -> text)."""


class StateStore:
    def __init__(self, db):
        self.db = db

    def get(self, key):
        """The stored text for `key`, or None when nothing is stored."""
        with self.db.transaction() as cursor:
            cursor.execute("select value from collector_state where key = %s", (key,))
            row = cursor.fetchone()
        return None if row is None else str(row[0])

    def set(self, key, value):
        with self.db.transaction() as cursor:
            cursor.execute("""
        insert into collector_state (key, value, updated_at) values (%s, %s, now())
        on conflict (key) do update set value = excluded.value, updated_at = now()""",
                           (key, str(value)))
