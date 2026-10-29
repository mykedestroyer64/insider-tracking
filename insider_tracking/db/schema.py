"""
schema.py - the tables, created (and kept up to date) automatically at the start of every run.

Every statement is safe to run again: "create table if not exists", "add column if not
exists", "drop index if exists". Changing the tables means adding a statement at the end of
the list (and asking the owner first when it removes anything).

The statements lock most tables while they run, and a run that locks them while another
collector is saving can make the two wait for each other ("deadlock detected": one of them
fails). So they only run when the tables aren't up to date yet: when one of them is missing,
or when the list has changed since it was last run (its version is kept in collector_state as
schema_version). Two runs that both find the tables out of date take turns.

Tables:
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
  trade_returns    how each traded stock did 1 month, 6 months and 1 year after the trade, and the
                   S&P 500 (SPY) over the same days (from Sharadar's prices; the prices aren't stored)
  collector_runs   one row per run of a collector, with its verdict
  collector_state  the collectors' bookmarks (how far back the history is listed)

Row Level Security is switched on for every table and no access rules are added, so nobody
can read the data through Supabase's public API. The collectors connect as the database
owner, which is not affected by that.
"""

import hashlib
import re


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
    """
    create table if not exists trade_returns (
        stock_key   text not null,
        trade_date  date not null,
        security    text,
        r30         real,
        r182        real,
        r365        real,
        m30         real,
        m182        real,
        m365        real,
        note        text,
        checked_on  date not null,
        primary key (stock_key, trade_date)
    )""",
    "alter table trade_returns enable row level security",
    "comment on table trade_returns is 'How a stock did 30, 182 and 365 days after a trade date (r, fractions) "
    "and SPY over the same days (m), from Sharadar adjusted closes. stock_key: T:<ticker> for a ticker in a "
    "Congress report, C:<CIK> for a company in an SEC Form 4. security: the Sharadar ticker used. checked_on: "
    "the market day the numbers were worked out on. Prices are not stored.'",
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


TABLES = tuple(re.findall(r"create table if not exists (\w+)", "\n".join(SCHEMA)))
VERSION = hashlib.sha256("\n".join(SCHEMA).encode("utf-8")).hexdigest()[:16]   # changes with the list
VERSION_KEY = "schema_version"
TURNS = 80_317_441          # the lock that makes two runs bringing the tables up to date take turns


def up_to_date(cursor):
    """Every table is there and the statements of this version have run."""
    cursor.execute("select count(*) from pg_tables where schemaname = 'public' and tablename = any(%s)",
                   (list(TABLES),))
    if int(cursor.fetchone()[0]) != len(TABLES):
        return False
    cursor.execute("select value from collector_state where key = %s", (VERSION_KEY,))
    row = cursor.fetchone()
    return row is not None and str(row[0]) == VERSION


def ensure_schema(db):
    """Create the tables if they don't exist yet, and bring them up to date (safe every time).
    Returns True when the statements ran, False when the tables were already up to date."""
    with db.transaction() as cursor:       # a quick look first, in its own transaction
        if up_to_date(cursor):
            return False
    with db.transaction() as cursor:
        # take turns before touching any table: a run waiting here holds no lock another needs
        cursor.execute("select pg_advisory_xact_lock(%s)", (TURNS,))
        if up_to_date(cursor):             # another run did it while this one waited
            return False
        for statement in SCHEMA:
            cursor.execute(statement)
        cursor.execute("""
        insert into collector_state (key, value, updated_at) values (%s, %s, now())
        on conflict (key) do update set value = excluded.value, updated_at = now()""", (VERSION_KEY, VERSION))
    return True
