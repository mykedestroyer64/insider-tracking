"""
platforms.py - the outside world the code under test gets: the test database instead of
Supabase, the test's own settings, a clock, a temporary output folder, and no waiting.
"""

import tempfile
from pathlib import Path

from insider_tracking.core.clock import SystemClock
from insider_tracking.db.connection import parse_db_url
from insider_tracking.runs.platform import Platform

# Settings that look like Supabase's: the collectors check them as usual, and the connection
# goes to the test database instead.
SUPABASE_SETTINGS = {
    "SUPABASE_DB_URL": "postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
    "SUPABASE_DB_PASSWORD": "pa@ss:w/rd#1",
}
EMAIL = "tester@example.com"


def settings(**more):
    """Settings as GitHub Actions has them: the database, a contact email, and `more`."""
    return {**SUPABASE_SETTINGS, "EFD_CONTACT_EMAIL": EMAIL, "GITHUB_ACTIONS": "true", **more}


def test_platform(database, environ, clock=None, output_dir=None, wrap=None, sleep=None):
    """A Platform whose database connections go to `database` (a TestDatabase).

    wrap(connection) may wrap each new connection (e.g. a FailingConnection); sleep defaults
    to not waiting at all."""
    def connect(url, password=None):
        parse_db_url(url, password)            # the settings are still checked, as in a real run
        connection = database.connect()
        return wrap(connection) if wrap else connection

    return Platform(environ=environ, connect=connect, clock=clock or SystemClock(),
                    output_dir=Path(output_dir) if output_dir else Path(tempfile.mkdtemp()),
                    sleep=sleep or (lambda seconds: None))
