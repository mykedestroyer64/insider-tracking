"""
rows.py - the rows the website reads from the database, and where they come from.

Each query the pages need is a SiteQuery, defined next to the loader that reads its rows. A
loader asks a RowSource for them:

  LiveRows     asks the database for every row, every time
  CopiedRows   (datacopy.py) keeps a copy between builds and downloads only what changed

Both give the same rows in the same order, so the pages don't depend on which one is used.
"""

from dataclasses import dataclass
from typing import Protocol

from .loading import column_exists, fetch_text, table_exists


@dataclass(frozen=True)
class SiteQuery:
    """A query the website reads: select `columns` from `tables` [where `where`] order by `order`.

    part: a text expression that splits the rows into parts (for example their day), so a copy
    can download only the parts that changed. The order must start with the same value, in the
    same direction (descending=True: newest first, rows without one last), so the parts, put
    one after the other, give the rows in the query's own order. None: always read whole."""

    name: str
    columns: str
    tables: str
    order: str = ""
    where: str = ""
    part: str | None = None
    descending: bool = False

    @property
    def sql(self):
        """The whole query."""
        return (f"select {self.columns} from {self.tables}" + (f" where {self.where}" if self.where else "")
                + (f" order by {self.order}" if self.order else ""))

    @property
    def part_sql(self):
        return f"coalesce({self.part}, '')"

    @property
    def fingerprint_sql(self):
        """Each part with its number of rows and the sum of their hashes, in the order of the parts."""
        inner = (f"select {self.part_sql} as part, {self.columns} from {self.tables}"
                 + (f" where {self.where}" if self.where else ""))
        return (f"select part, count(*)::text, sum(hashtext(q::text)::bigint)::text from ({inner}) q "
                f"group by part order by part{' desc' if self.descending else ''}")

    def parts_sql(self, every=False):
        """The rows of some parts (a list given as the query's parameter), or of all, each with its part first."""
        conditions = [c for c in (self.where, "" if every else f"{self.part_sql} = any(%s)") if c]
        return (f"select {self.part_sql}, {self.columns} from {self.tables}"
                + (" where " + " and ".join(conditions) if conditions else "")
                + (f" order by {self.order}" if self.order else ""))


class RowSource(Protocol):
    def rows(self, query: SiteQuery) -> list: ...

    def has_table(self, name: str) -> bool: ...

    def has_column(self, table: str, column: str) -> bool: ...


class LiveRows:
    """Every row from the database, every time."""

    def __init__(self, db):
        self.db = db

    def rows(self, query):
        return fetch_text(self.db, query.sql)

    def has_table(self, name):
        return table_exists(self.db, name)

    def has_column(self, table, column):
        return column_exists(self.db, table, column)


def wire_bytes(rows):
    """About how many bytes the database sends for these rows (each value, its length, each row's header)."""
    return sum(7 + sum(4 + len(value or "") for value in row) for row in rows)
