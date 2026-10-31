"""
datacopy.py - a copy of the website's rows kept between builds, so that a build downloads only
what changed since the last one.

Supabase's free plan allows 5 GB of downloads a month, and the rows the pages need come to a
few hundred MB. So GitHub keeps a copy between builds (.github/workflows/site.yml), and each
query's rows are split into parts (SiteQuery.part: most by day). For every part the database
sends only a fingerprint, worked out on its side: how many rows it has and the sum of their
hashes. A build downloads the parts whose fingerprint isn't the one in the copy, and takes the
others from the copy. A new, changed or deleted row changes its part's fingerprint, so the
copy always ends up the same as the database, row for row and in the same order.

The copy is a gzip-compressed JSON file. A copy that can't be read, or a query whose text has
changed since the copy was made, is simply downloaded again.
"""

import gzip
import hashlib
import json
import os
from pathlib import Path

from ..core.text import plural
from .rows import LiveRows, wire_bytes

FORMAT = 1


def definition(query):
    """What a query's copy is only good for: this exact query, split this way."""
    text = "\n".join((query.sql, query.part or "", str(query.descending)))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class DataCopy:
    """{query name: {"definition": ..., "parts": {part: {"count", "hash", "rows"}}}}, the parts
    in the query's order."""

    def __init__(self, queries=None):
        self.queries = queries if queries is not None else {}

    @classmethod
    def load(cls, path, log):
        """The copy saved at `path` (an empty one when there is none, or it can't be used)."""
        path = Path(path)
        if not path.exists():
            log("  no copy of the data yet: everything is downloaded")
            return cls()
        try:
            with gzip.open(path, "rt", encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("queries"), dict):
                raise ValueError("not a copy made by this version")
            return cls(data["queries"])
        except (OSError, EOFError, ValueError) as problem:
            log(f"  the copy of the data can't be used ({problem}): everything is downloaded")
            return cls()

    def save(self, path):
        """Write the copy (to a new file first, so a build that stops halfway leaves the old one)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as file:
            json.dump({"format": FORMAT, "queries": self.queries}, file, separators=(",", ":"))
        os.replace(temporary, path)


def as_text(row):
    return tuple(None if value is None else str(value) for value in row)


class CopiedRows:
    """A RowSource (rows.py): the rows of the copy, brought up to date part by part."""

    def __init__(self, db, copy):
        self.db = db
        self.copy = copy
        self.live = LiveRows(db)
        self.read = set()              # the queries this build read
        self.downloaded_rows = 0
        self.downloaded_bytes = 0      # about how much the database sent, fingerprints included
        self.kept_rows = 0             # rows taken from the copy

    def rows(self, query):
        self.read.add(query.name)
        if query.part is None:                       # small: read whole every time
            rows = self.live.rows(query)
            self._sent(rows, data=True)
            return rows
        stored = self.copy.queries.get(query.name)
        if not isinstance(stored, dict) or stored.get("definition") != definition(query):
            stored = {"definition": definition(query), "parts": {}}
        old = stored["parts"]
        with self.db.transaction() as cursor:
            # one view of the database for the fingerprints and for the parts read after them
            cursor.execute("set transaction isolation level repeatable read")
            cursor.execute(query.fingerprint_sql)
            prints = [as_text(row) for row in cursor.fetchall()]
            changed = {part for part, count, hash_ in prints
                       if (old.get(part) or {}).get("count") != count or (old.get(part) or {}).get("hash") != hash_}
            fresh = {}
            if changed:
                every = len(changed) == len(prints)     # nothing in the copy to keep: one query for all
                cursor.execute(query.parts_sql(every), () if every else (sorted(changed),))
                downloaded = [as_text(row) for row in cursor.fetchall()]
                for row in downloaded:
                    fresh.setdefault(row[0], []).append(list(row[1:]))
                self._sent(downloaded, data=True)
        self._sent(prints, data=False)
        parts = {}
        for part, count, hash_ in prints:
            if part in changed:
                rows = fresh.get(part, [])
            else:
                rows = old[part]["rows"]
                self.kept_rows += len(rows)
            parts[part] = {"count": count, "hash": hash_, "rows": rows}
        stored["parts"] = parts
        self.copy.queries[query.name] = stored
        return [tuple(row) for part in parts.values() for row in part["rows"]]

    def _sent(self, rows, data):
        if data:
            self.downloaded_rows += len(rows)
        self.downloaded_bytes += wire_bytes(rows)

    def has_table(self, name):
        return self.live.has_table(name)

    def has_column(self, table, column):
        return self.live.has_column(table, column)

    def keep_only_read(self):
        """Drop from the copy the queries this build didn't read (a table that is gone, say)."""
        for name in list(self.copy.queries):
            if name not in self.read:
                del self.copy.queries[name]

    def summary(self):
        """'downloaded 1,234 rows (~0.4 MB) from the database; 1,000,000 rows from the last build's copy'."""
        return (f"downloaded {plural(self.downloaded_rows, 'row')} (~{self.downloaded_bytes / 1e6:.1f} MB) from the "
                f"database; {plural(self.kept_rows, 'row')} from the last build's copy")
