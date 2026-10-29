"""
loading.py - reading the database for the website.

Every query asks the database for text, so the same code works with any driver and in tests;
dates and numbers are parsed in the loaders, in one place.
"""

from datetime import date


def fetch_text(db, sql):
    """Every row of a query, each value as text (None stays None)."""
    with db.transaction() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    return [tuple(None if v is None else str(v) for v in row) for row in rows]


def table_exists(db, name):
    return fetch_text(db, f"select count(*)::text from information_schema.tables where table_name = '{name}'")[0][0] != "0"


def column_exists(db, table, column):
    return fetch_text(db, "select count(*)::text from information_schema.columns "
                          f"where table_name = '{table}' and column_name = '{column}'")[0][0] != "0"


def parse_date(text):
    return date.fromisoformat(text) if text else None


def parse_int(text):
    try:
        return int(text) if text not in (None, "") else None
    except ValueError:
        return None


def parse_float(text):
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None
