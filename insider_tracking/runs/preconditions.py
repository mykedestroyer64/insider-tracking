"""
preconditions.py - what must be in place before a run sends anything anywhere.

Each precondition looks at the settings and either lets the run go ahead (None) or returns a
Refusal: the lines to print, the end of the verdict line and the exit code. A collector lists
the preconditions it needs in its definition.
"""

from dataclasses import dataclass
from typing import Protocol

from ..db.connection import DatabaseError, parse_db_url


@dataclass(frozen=True)
class Refusal:
    lines: tuple
    outcome: str        # the end of the verdict line, e.g. "- settings missing"
    exit_code: int = 1


class Precondition(Protocol):
    def refusal(self, settings) -> Refusal | None:
        """None when the run may go ahead, else why it may not."""


class RequiredSettings:
    """The database settings (and, if asked, a valid contact email) must be there and look right."""

    def __init__(self, contact_email=True):
        self.contact_email = contact_email

    def refusal(self, settings):
        missing = []
        if self.contact_email and not settings.has_valid_email:
            missing.append("EFD_CONTACT_EMAIL: your contact email")
        if not settings.db_url:
            missing.append("SUPABASE_DB_URL: Supabase's Session pooler connection string")
        else:
            try:
                parse_db_url(settings.db_url, settings.db_password)
            except DatabaseError as problem:
                missing.append(str(problem))
        if not missing:
            return None
        return Refusal(("Nothing was done, because these settings are missing or wrong:",
                        *(f"  - {item}" for item in missing),
                        "On GitHub, add them under Settings > Secrets and variables > Actions."),
                       "- settings missing")


class SharadarKey:
    """No Sharadar key yet is not a failure: the run does nothing and says so."""

    def refusal(self, settings):
        if settings.api_key:
            return None
        return Refusal(("There is no SHARADAR_API_KEY secret yet, so nothing was done. Add it under Settings >",
                        "Secrets and variables > Actions once the Sharadar subscription has started."),
                       "- no Sharadar key yet", exit_code=0)


class DatabaseUrl:
    """Only the connection string's presence is checked; a wrong one fails at step 1 (DB_ERROR)."""

    def refusal(self, settings):
        if settings.db_url:
            return None
        return Refusal((), "- SUPABASE_DB_URL is missing")
