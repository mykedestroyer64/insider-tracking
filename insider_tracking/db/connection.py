"""
connection.py - opening an encrypted connection to the Supabase database.

A Supabase database is PostgreSQL. We connect with pg8000, a driver written in plain Python
(so it installs on any computer), through Supabase's "Session pooler": GitHub's servers can't
use Supabase's direct connection, which needs IPv6.
"""

import re
import ssl
from urllib.parse import unquote

from ..core.errors import DatabaseError

URL_PATTERN = re.compile(r"postgres(?:ql)?://(?P<user>[^:@/]+)(?::(?P<password>.*))?"
                         r"@(?P<host>[^:/@]+)(?::(?P<port>\d+))?/(?P<database>[^?#]*)")
PASSWORD_PLACEHOLDERS = {"[YOUR-PASSWORD]", "YOUR-PASSWORD", "[YOUR_PASSWORD]"}


def describe(error):
    """A short, readable message for a database error."""
    first = error.args[0] if error.args else None
    if isinstance(first, dict) and "M" in first:   # pg8000 puts the server's message under "M"
        return first["M"]
    return " ".join(f"{type(error).__name__}: {error}".split())[:200]


def parse_db_url(url, password=None):
    """Split a connection string like Supabase's into its parts.

    The string may still contain the [YOUR-PASSWORD] placeholder; the real password then
    comes from the separate password setting.
    """
    match = URL_PATTERN.fullmatch((url or "").strip()) or URL_PATTERN.match((url or "").strip())
    if not match:
        raise DatabaseError("SUPABASE_DB_URL doesn't look like a connection string "
                            "(it should start with postgresql://)")
    in_url = match.group("password")
    in_url = None if in_url is None or in_url in PASSWORD_PLACEHOLDERS else unquote(in_url)
    password = (password or "").strip("\r\n") or in_url
    if not password:
        raise DatabaseError("no database password: add the SUPABASE_DB_PASSWORD secret")
    return {
        "user": unquote(match.group("user")),
        "password": password,
        "host": match.group("host"),
        "port": int(match.group("port") or 5432),
        "database": match.group("database") or "postgres",
    }


def connect(url, password=None):
    """Open an encrypted connection to the database (a DB-API connection)."""
    settings = parse_db_url(url, password)
    try:
        import pg8000.dbapi   # imported here so the rest of the project works without it
    except ImportError:
        raise DatabaseError("the pg8000 library is not installed "
                            "(run: python -m pip install -r requirements.txt)") from None
    context = ssl.create_default_context()
    context.check_hostname = False        # encrypted without checking the certificate,
    context.verify_mode = ssl.CERT_NONE   # like PostgreSQL's usual "sslmode=require"
    try:
        return pg8000.dbapi.connect(ssl_context=context, timeout=60, **settings)
    except Exception as error:
        hint = ""
        if settings["host"].startswith("db.") and settings["host"].endswith(".supabase.co"):
            hint = (" - this is Supabase's Direct connection string, which GitHub can't use; "
                    "use the Session pooler string instead")
        elif not isinstance(error, (OSError, TimeoutError)) and type(error).__module__ in ("builtins",):
            hint = f" ({library_versions()})"      # a library problem rather than a network one
        raise DatabaseError(f"could not connect to {settings['host']}: "
                            f"{describe(error)}{hint}") from None


def library_versions():
    """'pg8000 1.31.5, scramp 1.4.17': the versions installed, for error messages."""
    import importlib.metadata
    found = []
    for name in ("pg8000", "scramp"):
        try:
            found.append(f"{name} {importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            found.append(f"{name} not installed")
    return ", ".join(found)
