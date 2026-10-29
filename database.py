"""
database.py - saves what the collector finds in your Supabase database.

A Supabase database is PostgreSQL. We connect with pg8000, a driver written in
plain Python (so it installs on any computer), through Supabase's "Session
pooler": GitHub's servers can't use Supabase's direct connection, which needs
IPv6.

Tables (created automatically on the first run):
  reports          one row per trade report (Senate or House: see "chamber");
                   status says what has been done with it
  transactions     the trades read from electronic reports
  report_pages     a compressed copy of each Senate report page, so it can be re-read later
                   (House reports are PDFs that stay online, so they aren't copied)
  insider_filings  one row per SEC Form 4 (company insiders) with buys or sells by officers or directors
  insider_trades   those buys and sells, with exact shares and prices
  mar_trades       European executives' buys and sells (EU Market Abuse Regulation, Art. 19),
                   one row per transaction line, with the country it was reported in
  fx_rates         the European Central Bank's daily euro exchange rates (1 EUR = rate units)
  collector_runs   one row per run of a collector, with its verdict
  collector_state  the collectors' bookmarks (how far back the history is listed)

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
    # Added for the House collector (safe to run again: existing rows become 'senate')
    "alter table reports add column if not exists chamber text not null default 'senate'",
    "alter table reports add column if not exists state_district text",
    "alter table collector_runs add column if not exists collector text",
    """
    create table if not exists insider_filings (
        accession      text primary key,
        form           text not null,
        filing_date    date,
        period         date,
        company_cik    text,
        company_name   text,
        ticker         text,
        plan_10b5_1    boolean,
        is_amendment   boolean not null default false,
        original_date  date,
        source         text not null,
        first_seen_at  timestamptz not null default now()
    )""",
    """
    create table if not exists insider_trades (
        accession          text not null references insider_filings (accession) on delete cascade,
        row_key            text not null,
        insider_cik        text,
        insider_name       text,
        insider_title      text,
        roles              text[] not null default '{}',
        trade_date         date,
        code               text not null,
        kind               text not null,
        shares             double precision,
        price              double precision,
        value              double precision,
        acquired_disposed  text,
        shares_after       double precision,
        direct             boolean not null default true,
        nature             text,
        security           text,
        suspect            boolean not null default false,
        suspect_reason     text,
        primary key (accession, row_key)
    )""",
    """
    create table if not exists mar_trades (
        country             text not null,
        row_key             text not null,
        filing_key          text not null,
        published_at        timestamptz,
        issuer_name         text,
        issuer_short        text,
        issuer_lei          text,
        notifier            text,
        person_name         text not null,
        position            text,
        role                text,
        position_raw        text,
        closely_associated  boolean not null default false,
        is_amendment        boolean not null default false,
        amendment_note      text,
        initial             boolean,
        option_programme    boolean not null default false,
        nature              text,
        kind                text not null,
        instrument_type     text,
        instrument_name     text,
        isin                text,
        trade_date          date,
        volume              double precision,
        unit                text,
        price               double precision,
        currency            text,
        value               double precision,
        value_eur           double precision,
        venue               text,
        status              text not null default 'Current',
        suspect             boolean not null default false,
        suspect_reason      text,
        source              text not null,
        first_seen_at       timestamptz not null default now(),
        last_seen_at        timestamptz not null default now(),
        primary key (country, row_key)
    )""",
    """
    create table if not exists fx_rates (
        day       date not null,
        currency  text not null,
        rate      double precision not null,
        primary key (day, currency)
    )""",
    "alter table mar_trades enable row level security",
    "alter table fx_rates enable row level security",
    "comment on table mar_trades is 'European executives'' buys and sells of their company''s shares, reported "
    "under Art. 19 of the EU Market Abuse Regulation. country: SE (Finansinspektionen). One row per transaction "
    "line of a notification; status Current/Revised/Cancelled as in the register; suspect marks impossible numbers.'",
    "comment on table fx_rates is 'ECB euro reference rates: 1 EUR = rate units of the currency on that day.'",
    "create index if not exists reports_status_received on reports (status, date_received desc)",
    "alter table insider_filings enable row level security",
    "alter table insider_trades enable row level security",
    "comment on table insider_filings is 'SEC Form 4 filings (and amendments 4/A) by company insiders that hold "
    "buys or sells by an officer or director. source: the quarter file or daily list it came from.'",
    "comment on table insider_trades is 'Officers'' and directors'' open-market buys (P) and sells (S) from "
    "Form 4. suspect marks rows with impossible numbers, kept but not counted.'",
    "create index if not exists reports_chamber_status on reports (chamber, status, date_received desc)",
    "alter table reports enable row level security",
    "alter table transactions enable row level security",
    "alter table report_pages enable row level security",
    "alter table collector_runs enable row level security",
    "alter table collector_state enable row level security",
    # Space savings (29 Oct 2026; the free plan allows 500 MB): two columns nobody read and eleven
    # indexes nobody used. The site reads whole tables and the collectors look rows up by their keys.
    # A filing's address on EDGAR comes from its CIK and accession number; the list of every owner on
    # a Form 4 (funds and co-filers the site leaves out) can be loaded again from the SEC if ever needed.
    "alter table insider_filings drop column if exists owners",
    "alter table insider_filings drop column if exists url",
    "drop index if exists mar_trades_date",
    "drop index if exists mar_trades_person",
    "drop index if exists mar_trades_isin",
    "drop index if exists mar_trades_published",
    "drop index if exists insider_filings_date",
    "drop index if exists insider_filings_ticker",
    "drop index if exists insider_trades_insider",
    "drop index if exists insider_trades_date",
    "drop index if exists reports_last_name",
    "drop index if exists transactions_ticker",
    "drop index if exists transactions_date",
    # Descriptions you can see in Supabase's Table Editor
    "comment on table reports is 'One row per trade report. chamber: senate (eFD) or house "
    "(House Clerk). status: to_read (electronic, not read yet), read, failed (see note), "
    "not_read (scanned paper filing), other.'",
    "comment on table transactions is 'Trades read from electronic reports. raw holds every "
    "cell exactly as written on the page.'",
    "comment on table report_pages is 'Compressed (gzip) copy of each report page as "
    "downloaded.'",
    "comment on table collector_runs is 'One row per collector run, with its verdict.'",
    "comment on table collector_state is 'Bookmarks the collector uses between runs.'",
]

REPORT_COLUMNS = ["report_path", "report_uuid", "kind", "status", "first_name", "last_name",
                  "filer_name", "filer_type", "title", "is_amendment", "date_received", "url",
                  "chamber", "state_district"]
REPORT_DEFAULTS = {"chamber": "senate", "state_district": None}
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


# -------------------------------------------------------------------- tables

@handles_errors
def ensure_schema(conn):
    """Create the tables if they don't exist yet (safe to run every time)."""
    cursor = conn.cursor()
    for statement in SCHEMA:
        cursor.execute(statement)
    conn.commit()


@handles_errors
def counts(conn, chamber="senate"):
    """How many of a chamber's reports there are in each state, and how many transactions."""
    cursor = conn.cursor()
    cursor.execute("""
        select count(*),
               count(*) filter (where status = 'read'),
               count(*) filter (where status = 'to_read'),
               count(*) filter (where status = 'not_read'),
               count(*) filter (where status = 'failed')
        from reports where chamber = %s""", (chamber,))
    reports, read, to_read, paper, failed = (int(value) for value in cursor.fetchone())
    cursor.execute("""
        select count(*) from transactions t join reports r on r.report_path = t.report_path
        where r.chamber = %s""", (chamber,))
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
            values = dict(REPORT_DEFAULTS, **report)
            values["status"] = STATUS_FOR_KIND.get(report["kind"], "other")
            params += [values[column] for column in REPORT_COLUMNS]
        cursor.execute(
            f"insert into reports ({', '.join(REPORT_COLUMNS)}) values "
            + ", ".join([row_slots] * len(chunk))
            + " on conflict (report_path) do nothing returning report_path", params)
        new_paths.update(str(row[0]) for row in cursor.fetchall())
    conn.commit()
    return [report for report in reports if report["report_path"] in new_paths]


@handles_errors
def reports_to_read(conn, limit, chamber="senate"):
    """A chamber's electronic reports that haven't been read yet, newest first."""
    cursor = conn.cursor()
    cursor.execute("""
        select report_path, url, first_name, last_name, date_received
        from reports
        where status = 'to_read' and chamber = %s
        order by date_received desc nulls last, report_path
        limit %s""", (chamber, int(limit)))
    rows = cursor.fetchall()
    conn.commit()
    names = ["report_path", "url", "first_name", "last_name", "date_received"]
    return [dict(zip(names, row)) for row in rows]


@handles_errors
def save_report(conn, report_path, transactions, page, note=None, is_amendment=None):
    """Save a report's transactions (and page, if given) and mark it as read - all or nothing."""
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
    if page is not None:
        _store_page(cursor, report_path, page)
    cursor.execute("""
        update reports set status = 'read', read_at = now(), transaction_count = %s, note = %s,
                           is_amendment = coalesce(%s, is_amendment)
        where report_path = %s""", (len(transactions), note, is_amendment, report_path))
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


@handles_errors
def mark_scanned(conn, report_path, reason):
    """A report listed as electronic turned out to be a scan: keep it as not read (no text)."""
    cursor = conn.cursor()
    cursor.execute("""
        update reports set status = 'not_read', kind = 'paper', read_at = now(), note = %s
        where report_path = %s""", (reason[:500], report_path))
    conn.commit()


def _store_page(cursor, report_path, page):
    cursor.execute("""
        insert into report_pages (report_path, html_gzip, fetched_at) values (%s, %s, now())
        on conflict (report_path) do update
            set html_gzip = excluded.html_gzip, fetched_at = now()""",
                   (report_path, gzip.compress(page)))


# ---------------------------------------------------------- company insiders

FILING_COLUMNS = ["accession", "form", "filing_date", "period", "company_cik", "company_name", "ticker",
                  "plan_10b5_1", "is_amendment", "original_date", "source"]
INSIDER_TRADE_COLUMNS = ["accession", "row_key", "insider_cik", "insider_name", "insider_title", "roles",
                         "trade_date", "code", "kind", "shares", "price", "value", "acquired_disposed",
                         "shares_after", "direct", "nature", "security", "suspect", "suspect_reason"]


@handles_errors
def save_insider_filings(conn, filings, trades, source):
    """Store Form 4 filings and their buys and sells (a filing seen before is replaced).

    Returns how many filings were new.
    """
    if not filings:
        return 0
    cursor = conn.cursor()
    new = 0
    by_filing = {}
    for trade in trades:
        by_filing.setdefault(trade["accession"], []).append(trade)
    for start in range(0, len(filings), 50):
        chunk = filings[start:start + 50]
        slots = "(" + ", ".join(["%s"] * len(FILING_COLUMNS)) + ")"
        params = []
        for filing in chunk:
            values = dict(filing, source=source)
            params += [values[column] for column in FILING_COLUMNS]
        cursor.execute(
            f"insert into insider_filings ({', '.join(FILING_COLUMNS)}) values " + ", ".join([slots] * len(chunk))
            + " on conflict (accession) do update set form = excluded.form, filing_date = excluded.filing_date, "
            "period = excluded.period, company_cik = excluded.company_cik, company_name = excluded.company_name, "
            "ticker = excluded.ticker, plan_10b5_1 = excluded.plan_10b5_1, is_amendment = excluded.is_amendment, "
            "original_date = excluded.original_date, source = excluded.source returning (xmax = 0)", params)
        new += sum(1 for row in cursor.fetchall() if row[0] in (True, "t", "true", 1))
        accessions = [filing["accession"] for filing in chunk]
        cursor.execute("delete from insider_trades where accession = any(%s)", (accessions,))
        rows = [trade for filing in chunk for trade in by_filing.get(filing["accession"], [])]
        for rstart in range(0, len(rows), 100):
            part = rows[rstart:rstart + 100]
            slots = "(" + ", ".join(["%s"] * len(INSIDER_TRADE_COLUMNS)) + ")"
            tparams = []
            for trade in part:
                tparams += [trade[column] for column in INSIDER_TRADE_COLUMNS]
            cursor.execute(f"insert into insider_trades ({', '.join(INSIDER_TRADE_COLUMNS)}) values "
                           + ", ".join([slots] * len(part)), tparams)
    conn.commit()
    return new


@handles_errors
def remove_entity_insiders(conn, pattern):
    """Delete trades attributed to funds or companies (saved before the rule that keeps people only).

    `pattern` is a PostgreSQL regular expression; returns how many trades went."""
    cursor = conn.cursor()
    cursor.execute("delete from insider_trades where insider_name ~* %s or insider_name ~ '^\\d' returning 1",
                   (pattern,))
    removed = len(cursor.fetchall())
    cursor.execute("delete from insider_filings f where not exists "
                   "(select 1 from insider_trades t where t.accession = f.accession)")
    conn.commit()
    return removed or 0


@handles_errors
def insider_counts(conn):
    cursor = conn.cursor()
    cursor.execute("select count(*), count(*) filter (where is_amendment), min(filing_date), max(filing_date) "
                   "from insider_filings")
    filings, amendments, first, last = cursor.fetchone()
    cursor.execute("select count(*), count(*) filter (where kind = 'buy'), count(*) filter (where kind = 'sell'), "
                   "count(*) filter (where suspect), count(distinct insider_cik), count(distinct accession) "
                   "from insider_trades")
    trades, buys, sells, suspect, insiders, _ = cursor.fetchone()
    cursor.execute("select pg_size_pretty(pg_total_relation_size('insider_trades') + "
                   "pg_total_relation_size('insider_filings'))")
    size = cursor.fetchone()[0]
    conn.commit()
    return {"filings": int(filings), "amendments": int(amendments), "first_filing": first, "last_filing": last,
            "trades": int(trades), "buys": int(buys), "sells": int(sells), "suspect": int(suspect),
            "insiders": int(insiders), "size": str(size)}


# ------------------------------------------------------- European executives

MAR_COLUMNS = ["country", "row_key", "filing_key", "published_at", "issuer_name", "issuer_short", "issuer_lei",
               "notifier", "person_name", "position", "role", "position_raw", "closely_associated", "is_amendment",
               "amendment_note", "initial", "option_programme", "nature", "kind", "instrument_type",
               "instrument_name", "isin", "trade_date", "volume", "unit", "price", "currency", "value", "value_eur",
               "venue", "status", "suspect", "suspect_reason", "source"]
MAR_UPDATED = ["status", "amendment_note", "value_eur", "suspect", "suspect_reason", "source", "issuer_short",
               "position", "role", "notifier", "person_name", "issuer_name"]


@handles_errors
def save_mar_trades(conn, rows, source):
    """Store transaction lines from a European register (a line seen before is updated).

    Returns (new rows, updated rows)."""
    if not rows:
        return 0, 0
    cursor = conn.cursor()
    new = updated = 0
    for start in range(0, len(rows), 100):
        chunk = rows[start:start + 100]
        slots = "(" + ", ".join(["%s"] * len(MAR_COLUMNS)) + ")"
        params = []
        for row in chunk:
            values = dict(row, source=source)
            params += [values.get(column) for column in MAR_COLUMNS]
        updates = ", ".join(f"{c} = excluded.{c}" for c in MAR_UPDATED)
        cursor.execute(
            f"insert into mar_trades ({', '.join(MAR_COLUMNS)}) values " + ", ".join([slots] * len(chunk))
            + f" on conflict (country, row_key) do update set {updates}, last_seen_at = now() "
            "returning (xmax = 0)", params)
        for (is_new,) in cursor.fetchall():
            if is_new in (True, "t", "true", 1):
                new += 1
            else:
                updated += 1
    conn.commit()
    return new, updated


@handles_errors
def mar_rows_without_eur(conn, country):
    """Rows whose euro value could not be worked out yet (no exchange rate at the time)."""
    cursor = conn.cursor()
    cursor.execute("select row_key, trade_date, currency, value from mar_trades "
                   "where country = %s and value is not null and value_eur is null and currency is not null",
                   (country,))
    rows = cursor.fetchall()
    conn.commit()
    return [{"row_key": str(r[0]), "trade_date": r[1], "currency": str(r[2]), "value": float(r[3])} for r in rows]


@handles_errors
def set_mar_eur(conn, country, values):
    """values: {row_key: value_eur}."""
    cursor = conn.cursor()
    for row_key, value_eur in values.items():
        cursor.execute("update mar_trades set value_eur = %s where country = %s and row_key = %s",
                       (value_eur, country, row_key))
    conn.commit()


@handles_errors
def mar_counts(conn, country):
    cursor = conn.cursor()
    cursor.execute("""
        select count(*), count(*) filter (where kind = 'buy'), count(*) filter (where kind = 'sell'),
               count(*) filter (where suspect), count(*) filter (where status <> 'Current'),
               count(distinct person_name), count(distinct coalesce(issuer_lei, issuer_name)),
               min(published_at), max(published_at)
        from mar_trades where country = %s""", (country,))
    trades, buys, sells, suspect, not_current, people, companies, first, last = cursor.fetchone()
    cursor.execute("select pg_size_pretty(pg_total_relation_size('mar_trades'))")
    size = cursor.fetchone()[0]
    conn.commit()
    return {"trades": int(trades), "buys": int(buys), "sells": int(sells), "suspect": int(suspect),
            "not_current": int(not_current), "people": int(people), "companies": int(companies),
            "first_published": first, "last_published": last, "size": str(size)}


@handles_errors
def save_fx_rates(conn, rows):
    """rows: [(day, currency, rate)]. Returns how many were new."""
    if not rows:
        return 0
    cursor = conn.cursor()
    new = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        params = []
        for day, currency, rate in chunk:
            params += [day, currency, rate]
        cursor.execute("insert into fx_rates (day, currency, rate) values " + ", ".join(["(%s, %s, %s)"] * len(chunk))
                       + " on conflict (day, currency) do update set rate = excluded.rate returning (xmax = 0)", params)
        new += sum(1 for (is_new,) in cursor.fetchall() if is_new in (True, "t", "true", 1))
    conn.commit()
    return new


@handles_errors
def load_fx_rates(conn):
    """All stored rates as [(day, currency, rate)]."""
    cursor = conn.cursor()
    cursor.execute("select day, currency, rate from fx_rates")
    rows = [(r[0] if not isinstance(r[0], str) else __import__("datetime").date.fromisoformat(r[0]), str(r[1]), float(r[2]))
            for r in cursor.fetchall()]
    conn.commit()
    return rows


@handles_errors
def total_size(conn):
    """The whole database's size in MB (Supabase's free plan allows 500 MB)."""
    cursor = conn.cursor()
    cursor.execute("select pg_database_size(current_database())")
    size = int(cursor.fetchone()[0])
    conn.commit()
    return round(size / 1024 / 1024)


FREE_PLAN_MB = 500


# ---------------------------------------------------------------------- runs

@handles_errors
def start_run(conn, ran_on, collector="senate"):
    cursor = conn.cursor()
    cursor.execute("insert into collector_runs (started_at, ran_on, collector) "
                   "values (now(), %s, %s) returning id", (ran_on, collector))
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
