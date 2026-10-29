"""
settings.py - the private settings a run needs, read from environment variables (GitHub
secrets on GitHub Actions):

  EFD_CONTACT_EMAIL     your email, sent in the User-Agent (or --email on the command line)
  SUPABASE_DB_URL       Supabase's "Session pooler" connection string
  SUPABASE_DB_PASSWORD  your Supabase database password
  SHARADAR_API_KEY      the Sharadar API key (returns collector only)
"""

import re
from dataclasses import dataclass

EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class Settings:
    email: str = ""
    db_url: str = ""
    db_password: str = ""
    api_key: str = ""

    @classmethod
    def read(cls, environ, email_argument=None):
        return cls(email=(email_argument or environ.get("EFD_CONTACT_EMAIL") or "").strip(),
                   db_url=(environ.get("SUPABASE_DB_URL") or "").strip(),
                   db_password=environ.get("SUPABASE_DB_PASSWORD") or "",
                   api_key=(environ.get("SHARADAR_API_KEY") or "").strip())

    @property
    def has_valid_email(self):
        return bool(EMAIL_PATTERN.fullmatch(self.email))


def ran_on(environ):
    """'GitHub Actions' or 'your computer': said in every verdict line."""
    return "GitHub Actions" if environ.get("GITHUB_ACTIONS") == "true" else "your computer"
