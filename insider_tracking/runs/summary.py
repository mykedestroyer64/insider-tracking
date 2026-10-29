"""summary.py - pieces of the lines that sum a run up."""

from ..db.connection import DatabaseError


def whole_database_note(size):
    """' (whole database 318 MB of the free plan's 500 MB)', or '' if the size can't be read."""
    try:
        return size.whole_database_note()
    except DatabaseError:
        return ""
