"""size.py - how big the database is (Supabase's free plan allows 500 MB)."""

FREE_PLAN_MB = 500


class DatabaseSize:
    def __init__(self, db):
        self.db = db

    def total_mb(self):
        """The whole database's size in MB, rounded."""
        with self.db.transaction() as cursor:
            cursor.execute("select pg_database_size(current_database())")
            size = int(cursor.fetchone()[0])
        return round(size / 1024 / 1024)

    def whole_database_note(self):
        """' (whole database 318 MB of the free plan's 500 MB)', for the end of a run's summary."""
        return f" (whole database {self.total_mb()} MB of the free plan's {FREE_PLAN_MB} MB)"
