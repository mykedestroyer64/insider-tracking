"""
database.py - saves what the collector finds in your Supabase database.

A Supabase database is PostgreSQL. We connect with pg8000, a driver written in
plain Python (so it installs on any computer), through Supabase's "Session
pooler": GitHub's servers can't use Supabase's direct connection, which needs
IPv6.

Tables (created automatically on the first run):
  reports          one row per eFD report; status says what has been done with it
  transactions     the trades read from electronic reports
  report_pages     a compressed copy of each report page, so it can be re-read later
  collector_runs   one row per run of the collector, with its verdict
  collector_state  the collector's bookmarks (how far back the history is listed)

Row Level Security is switched on for every table and no access rules are
added, so nobody can read the data through Supabase's public API yet. The
collector connects as the database owner, which is not affected by that.
"""

import functools
import gzip
import json
import re
import ssl
from urllib.parse import unquote

SCHEMA = [
    """
    create table if not exists reports (
        report_path        text primary key,
        report_uuid        text not null,
        kind               text not null,
        status             text not null,
        first_name         text,
        last_name          text,
        filer_name         text,
        filer_type         text,
        title              text,
        is_amendment       boolean not null default false,
        date_received      date,
        url                text not null,
        transaction_count  integer,
        note               text,
        first_seen_at      timestamptz not null default now(),
        read_at            timestamptz
    )""",
    """
    create table if not exists transactions (
        report_path       text not null references reports (report_path) on delete cascade,
        row_number        integer not null,
        transaction_date  date,
        owner             text,
        ticker            text,
        asset_name        text,
        asset_type        text,
        transaction_type  text,
        trade_type        text,
        amount            text,
        amount_min        bigint,
        amount_max        bigint,
        comment           text,
        raw               jsonb not null default '{}'::jsonb,
        primary key (report_path, row_number)
    )""",
    """
    create table if not exists report_pages (
        report_path  text primary key references reports (report_path) on delete cascade,
        html_gzip    bytea not null,
        fetched_at   timestamptz not null default now()
    )""",
    """
    create table if not exists collector_runs (
        id            bigserial primary key,
        started_at    timestamptz not null default now(),
        finished_at   timestamptz,
        ran_on        text,
        verdict       text,
        verdict_line  text,
        summary       jsonb
    )""",
    """
    create table if not exists collector_state (
        key         text primary key,
        value       text not null,
        updated_at  timestamptz not null default now()
    )""",
    "create index if not exists reports_status_received on reports (status, date_received desc)",
    "create index if not exists reports_last_name on reports (last_name)",
    "create index if not exists transactions_ticker on transactions (ticker)",
    "create index if not exists transactions_date on transactions (transaction_date)",
    "alter table reports enable row level security",
    "alter table transactions enable row level security",
    "alter table report_pages enable row level security",
    "alter table collector_runs enable row level security",
    "alter table collector_state enable row level security",
    # Descriptions you can see in Supabase's Table Editor
    "comment on table reports is 'One row per eFD report. status: to_read (electronic, not "
    "read yet), read, failed (see note), not_read (scanned paper filing), other.'",
    "comment on table transactions is 'Trades read from electronic reports. raw holds every "
    "cell exactly as written on the page.'",
    "comment on table report_pages is 'Compressed (gzip) copy of each report page as "
    "downloaded.'",
    "comment on table collector_runs is 'One row per collector run, with its verdict.'",
    "comment on table collector_state is 'Bookmarks the collector uses between runs.'",
]

REPORT_COLUMNS = ["report_path", "report_uuid", "kind", "status", "first_name", "last_name",
                  "filer_name", "filer_type", "title", "is_amendment", "date_received", "url"]
TRANSACTION_COLUMNS = ["report_path", "row_number", "transaction_date", "owner", "ticker",
                       "asset_name", "asset_type", "transaction_type", "trade_type", "amount",
                       "amount_min", "amount_max", "comment", "raw"]
STATUS_FOR_KIND = {"electronic": "to_read", "paper": "not_read"}

URL_PATTERN = re.compile(r"postgres(?:ql)?://(?P<user>[^:@/]+)(?::(?P<password>.*))?"
                         r"@(?P<host>[^:/@]+)(?::(?P<port>\d+))?/(?P<database>[^?#]*)")
PASSWORD_PLACEHOLDERS = {"[YOUR-PASSWORD]", "YOUR-PASSWORD", "[YOUR_PASSWORD]"}


class DatabaseError(Exception):
    """Any problem with the database (connecting, reading or writing)."""


def describe(error):
    """A short, readable message for a database error."""
    first = error.args[0] if error.args else None
    if isinstance(first, dict) and "M" in first:   # pg8000 puts the server's message under "M"
        return first["M"]
    return " ".join(f"{type(error).__name__}: {error}".split())[:200]


def handles_errors(function):
    """Turn any driver error into a DatabaseError (and undo the unfinished change)."""
    @functools.wraps(function)
    def wrapper(conn, *args, **kwargs):
        try:
            return function(conn, *args, **kwargs)
        except DatabaseError:
            raise
        except Exception as error:
            try:
                conn.rollback()
            except Exception:
                pass
            raise DatabaseError(describe(error)) from error
    return wrapper


# ---------------------------------------------------------------- connecting

def parse_db_url(url, password=None):
    """Split a connection string like Supabase's into its parts.

    The string may still contain the [YOUR-PASSWORD] placeholder; the real
    password then comes from the separate password setting.
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
    """Open an encrypted connection to the database."""
    settings = parse_db_url(url, password)
    try:
        import pg8000.dbapi   # imported here so the rest of this file works without it
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
        raise DatabaseError(f"could not connect to {settings['host']}: "
                            f"{describe(error)}{hint}") from None


# -------------------------------------------------------------------- tables

@handles_errors
def ensure_schema(conn):
    """Create the tables if they don't exist yet (safe to run every time)."""
    cursor = conn.cursor()
    for statement in SCHEMA:
        cursor.execute(statement)
    conn.commit()


@handles_errors
def counts(conn):
    """How many reports there are in each state, and how many transactions."""
    cursor = conn.cursor()
    cursor.execute("""
        select count(*),
               count(*) filter (where status = 'read'),
               count(*) filter (where status = 'to_read'),
               count(*) filter (where status = 'not_read'),
               count(*) filter (where status = 'failed')
        from reports""")
    reports, read, to_read, paper, failed = (int(value) for value in cursor.fetchone())
    cursor.execute("select count(*) from transactions")
    transactions = int(cursor.fetchone()[0])
    conn.commit()
    return {"reports": reports, "read": read, "to_read": to_read, "paper_not_read": paper,
            "failed": failed, "transactions": transactions}


@handles_errors
def get_state(conn, key):
    cursor = conn.cursor()
    cursor.execute("select value from collector_state where key = %s", (key,))
    row = cursor.fetchone()
    conn.commit()
    return None if row is None else str(row[0])


@handles_errors
def set_state(conn, key, value):
    cursor = conn.cursor()
    cursor.execute("""
        insert into collector_state (key, value, updated_at) values (%s, %s, now())
        on conflict (key) do update set value = excluded.value, updated_at = now()""",
                   (key, str(value)))
    conn.commit()


# ------------------------------------------------------------------- reports

@handles_errors
def add_reports(conn, reports):
    """Store reports we haven't seen before. Returns the ones that were new."""
    new_paths = set()
    cursor = conn.cursor()
    for start in range(0, len(reports), 100):
        chunk = reports[start:start + 100]
        row_slots = "(" + ", ".join(["%s"] * len(REPORT_COLUMNS)) + ")"
        params = []
        for report in chunk:
            values = dict(report, status=STATUS_FOR_KIND.get(report["kind"], "other"))
            params += [values[column] for column in REPORT_COLUMNS]
        cursor.execute(
            f"insert into reports ({', '.join(REPORT_COLUMNS)}) values "
            + ", ".join([row_slots] * len(chunk))
            + " on conflict (report_path) do nothing returning report_path", params)
        new_paths.update(str(row[0]) for row in cursor.fetchall())
    conn.commit()
    return [report for report in reports if report["report_path"] in new_paths]


@handles_errors
def reports_to_read(conn, limit):
    """Electronic reports that haven't been read yet, newest first."""
    cursor = conn.cursor()
    cursor.execute("""
        select report_path, url, first_name, last_name, date_received
        from reports
        where status = 'to_read'
        order by date_received desc nulls last, report_path
        limit %s""", (int(limit),))
    rows = cursor.fetchall()
    conn.commit()
    names = ["report_path", "url", "first_name", "last_name", "date_received"]
    return [dict(zip(names, row)) for row in rows]


@handles_errors
def save_report(conn, report_path, transactions, page, note=None):
    """Save a report's transactions and page, and mark it as read - all or nothing."""
    cursor = conn.cursor()
    cursor.execute("delete from transactions where report_path = %s", (report_path,))
    row_slots = "(" + ", ".join(["%s"] * (len(TRANSACTION_COLUMNS) - 1)) + ", %s::jsonb)"
    for start in range(0, len(transactions), 100):
        chunk = transactions[start:start + 100]
        params = []
        for transaction in chunk:
            values = dict(transaction, report_path=report_path,
                          raw=json.dumps(transaction["raw"], ensure_ascii=False))
            params += [values[column] for column in TRANSACTION_COLUMNS]
        cursor.execute(f"insert into transactions ({', '.join(TRANSACTION_COLUMNS)}) values "
                       + ", ".join([row_slots] * len(chunk)), params)
    _store_page(cursor, report_path, page)
    cursor.execute("""
        update reports set status = 'read', read_at = now(), transaction_count = %s, note = %s
        where report_path = %s""", (len(transactions), note, report_path))
    conn.commit()


@handles_errors
def mark_failed(conn, report_path, reason, page=None):
    """Mark a report that couldn't be read, with the reason (and the page, if we got one)."""
    cursor = conn.cursor()
    if page:
        _store_page(cursor, report_path, page)
    cursor.execute("""
        update reports set status = 'failed', read_at = now(), note = %s
        where report_path = %s""", (reason[:500], report_path))
    conn.commit()


def _store_page(cursor, report_path, page):
    cursor.execute("""
        insert into report_pages (report_path, html_gzip, fetched_at) values (%s, %s, now())
        on conflict (report_path) do update
            set html_gzip = excluded.html_gzip, fetched_at = now()""",
                   (report_path, gzip.compress(page)))


# ---------------------------------------------------------------------- runs

@handles_errors
def start_run(conn, ran_on):
    cursor = conn.cursor()
    cursor.execute("insert into collector_runs (started_at, ran_on) values (now(), %s) "
                   "returning id", (ran_on,))
    run_id = int(cursor.fetchone()[0])
    conn.commit()
    return run_id


@handles_errors
def finish_run(conn, run_id, verdict, verdict_line, summary):
    cursor = conn.cursor()
    cursor.execute("""
        update collector_runs
        set finished_at = now(), verdict = %s, verdict_line = %s, summary = %s::jsonb
        where id = %s""", (verdict, verdict_line, json.dumps(summary, default=str), run_id))
    conn.commit()
