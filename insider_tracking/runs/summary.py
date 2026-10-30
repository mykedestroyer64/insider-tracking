"""summary.py - pieces of the lines that sum a run up."""

from ..core.errors import DatabaseError
from ..db.size import FREE_PLAN_MB


def whole_database_note(size):
    """' (whole database 318 MB of the free plan's 500 MB)', or '' if the size can't be read.
    size: a SizeGauge (sources/ports.py)."""
    try:
        return f" (whole database {size.total_mb()} MB of the free plan's {FREE_PLAN_MB} MB)"
    except DatabaseError:
        return ""
