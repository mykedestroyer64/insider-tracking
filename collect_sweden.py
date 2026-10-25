"""
collect_sweden.py - the Swedish executives collector (Finansinspektionen's PDMR register).

GitHub Actions runs this every 3 hours (see .github/workflows/collect-sweden.yml).
Each run:
  1. connects to the Supabase database (and adds the tables the first time)
  2. updates the euro exchange rates from the European Central Bank, so every
     trade in kronor (or dollars, or pounds) gets a euro figure next to it
  3. reads the register site's robots.txt, the rules it gives to scripts
  4. exports the notifications published in the last 45 days from the register
     (one request) and saves the buys and sells of shares in them
  5. loads the history, twelve months per run, back to July 2016 when the
     register started
Lines seen before are updated, never duplicated, so the windows can overlap and
a notification that is later revised or cancelled is marked as such.

It sends about 15 requests to Finansinspektionen per run, 2-3 seconds apart,
with a User-Agent that names the project and gives a contact email, and stops
if the site refuses it (no workarounds).

Settings: the same three GitHub secrets as the other collectors
(EFD_CONTACT_EMAIL, SUPABASE_DB_URL, SUPABASE_DB_PASSWORD).

The last line printed is the verdict. Exit code: 0 if OK, 1 otherwise.
"""

import argparse
import os
import re
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import database
import ecb_rates
import sweden_parsing
import sweden_site
from sweden_site import BLOCKED, CHANGED, OK, SITE_DOWN

PROJECT = "insider-tracking"
VERSION = "0.1"
COUNTRY = sweden_parsing.COUNTRY

MAX_REQUESTS = 60          # requests to Finansinspektionen (and the ECB) per run
EXPORT_CAP = 1000          # the register's export stops at this many lines: longer windows are split
HISTORY_VERSION = "2"      # bump to make the next run load the history again (v1 lost lines to the cap)
RECENT_DAYS = 45           # the window re-exported on every run (catches revisions and cancellations)
MONTHS_PER_RUN = 12        # history months loaded per run
FIRST_DAY = date(2016, 7, 3)   # the register starts with the Market Abuse Regulation
RATES_STALE_AFTER = 80     # days: with older rates than this the whole history file is fetched again
RATES_FROM = date(2015, 1, 1)   # rates older than this aren't kept

DB_ERROR, ERROR, NOT_RUN = "DB_ERROR", "ERROR", "NOT_RUN"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

STEP_NAMES = {
    1: "Connect to the database",
    2: "Update euro exchange rates (ECB)",
    3: "Check the register's rules for scripts",
    4: "Export the last 45 days",
    5: "Load history (12 months per run)",
}
TOTAL_STEPS = len(STEP_NAMES)

ADVICE = {
    BLOCKED: ["Finansinspektionen's site refused the collector, or its robots.txt rules scripts out.",
              "Do not add workarounds. The next scheduled run tries again in 3 hours; if several runs",
              "in a row are BLOCKED, stop the schedule and share this result. FI can be asked by email",
              "(finansinspektionen@fi.se) whether a daily export is acceptable."],
    SITE_DOWN: ["The site had a server error. The next scheduled run tries again in 3 hours."],
    CHANGED: ["A page or file did not look the way the collector expects, so the site may work",
              "differently now. Share this log so the collector can be updated."],
    DB_ERROR: ["Check the SUPABASE_DB_URL secret (it must be Supabase's Session pooler string),",
               "the SUPABASE_DB_PASSWORD secret, and that the Supabase project isn't paused."],
    ERROR: ["The collector hit a problem it didn't expect. Share this log."],
}


class StepLog:
    def __init__(self):
        self.steps = []

    def done(self, number, status, detail="", codes=()):
        codes = list(codes)
        http = f" (HTTP {' -> '.join(str(code) for code in codes)})" if codes else ""
        if detail.startswith("HTTP "):
            http = ""
        label = f"{STEP_NAMES[number]} ".ljust(42, ".")
        print(f"[{number}/{TOTAL_STEPS}] {label} {status}{http}" + (f" - {detail}" if detail else ""), flush=True)
        self.steps.append({"step": number, "name": STEP_NAMES[number], "status": status,
                           "http_codes": codes, "detail": detail})

    def rest(self, after, status):
        for number in range(after + 1, TOTAL_STEPS + 1):
            self.done(number, status)


def note(message):
    print(f"      {message}", flush=True)


def plural(count, word):
    return f"{count:,} {word}" + ("" if count == 1 else "s")


def as_date(value):
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value


def last_business_day(today):
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


# --------------------------------------------------------------------- steps

def update_rates(conn, web, today):
    """Step 2: make sure the euro rates cover every trade day; returns (Rates, detail)."""
    stored = database.load_fx_rates(conn)
    rates = ecb_rates.Rates(stored)
    latest = rates.latest_day()
    fetched = 0
    if latest is None or latest < today - timedelta(days=RATES_STALE_AFTER):
        response = web.get(sweden_site.ECB_HISTORY, who="the ECB")
        try:
            rows = ecb_rates.parse_history_zip(response.content)
        except ecb_rates.RatesProblem as problem:
            raise sweden_site.SiteStop(CHANGED, f"the ECB's rates history could not be read: {problem}", response) from None
        rows = [row for row in rows if row[0] >= RATES_FROM]      # the registers start in 2016
        fetched = database.save_fx_rates(conn, rows)
        what = f"loaded the whole history ({fetched:,} new daily rates)"
    elif latest < last_business_day(today):
        response = web.get(sweden_site.ECB_RECENT, who="the ECB")
        try:
            rows = ecb_rates.parse_xml(response.text)
        except ecb_rates.RatesProblem as problem:
            raise sweden_site.SiteStop(CHANGED, f"the ECB's recent rates could not be read: {problem}", response) from None
        fetched = database.save_fx_rates(conn, rows)
        what = f"{fetched:,} new daily rates"
    else:
        what = "already up to date"
    if fetched:
        rates = ecb_rates.Rates(database.load_fx_rates(conn))
    latest = rates.latest_day()
    # euro figures for rows saved while a rate was missing
    missing = database.mar_rows_without_eur(conn, COUNTRY)
    filled = {}
    for row in missing:
        rate = rates.rate_on(as_date(row["trade_date"]), row["currency"])
        if rate:
            filled[row["row_key"]] = row["value"] / rate
    if filled:
        database.set_mar_eur(conn, COUNTRY, filled)
    detail = f"{what}; newest rate {latest}" if latest else what
    if filled:
        detail += f"; euro values added to {plural(len(filled), 'older row')}"
    return rates, detail


def save_export(conn, web, start, end, rates, stats, label):
    """Export one window of the register and save it. Returns (rows kept, new, updated, lines).

    The register's export never holds more than EXPORT_CAP lines, so a window that
    comes back full is split in two and both halves are exported again."""
    response = web.export(start, end)
    try:
        rows, counts = sweden_parsing.parse_export(response.content, rates.rate_on)
    except sweden_parsing.ExportProblem as problem:
        raise sweden_site.SiteStop(CHANGED, f"the export for {start}..{end} could not be read: {problem}", response) from None
    if counts["lines"] >= EXPORT_CAP and end > start:
        middle = start + (end - start) // 2
        first = save_export(conn, web, start, middle, rates, stats, label)
        second = save_export(conn, web, middle + timedelta(days=1), end, rates, stats, label)
        return tuple(a + b for a, b in zip(first, second))
    if counts["lines"] >= EXPORT_CAP:
        note(f"{start}: {counts['lines']:,} lines on one day, the export's limit; some may be missing")
    new, updated = database.save_mar_trades(conn, rows, source=f"{label} {start}..{end}")
    stats["lines"] += counts["lines"]
    stats["kept"] += len(rows)
    stats["new"] += new
    stats["updated"] += updated
    stats["suspect"] += counts["suspect"]
    stats["skipped_nature"] += counts["other_nature"]
    stats["skipped_instrument"] += counts["other_instrument"]
    stats["skipped_entities"] += counts["entities"]
    return len(rows), new, updated, counts["lines"]


def export_recent(conn, web, rates, stats, today):
    """Step 4: the last RECENT_DAYS days of publications (one request)."""
    start = today - timedelta(days=RECENT_DAYS)
    kept, new, updated, lines = save_export(conn, web, start, today, rates, stats, "recent")
    if database.get_state(conn, "se_history_cursor") is None:
        database.set_state(conn, "se_history_cursor", start.isoformat())   # history continues from here
    database.set_state(conn, "se_recent_through", today.isoformat())
    return (f"{lines:,} lines published {start} to {today}; {kept:,} buys or sells of shares kept "
            f"({new:,} new, {updated:,} seen before)")


def load_history(conn, web, rates, stats):
    """Step 5: months before the loaded range, MONTHS_PER_RUN per run, back to FIRST_DAY."""
    if database.get_state(conn, "se_history_version") != HISTORY_VERSION:
        recent_from = as_date(database.get_state(conn, "se_recent_through")) or date.today()
        database.set_state(conn, "se_history_cursor", (recent_from - timedelta(days=RECENT_DAYS)).isoformat())
        database.set_state(conn, "se_history_version", HISTORY_VERSION)
        note("the history is loaded again from the start (the way it is read changed)")
    cursor = as_date(database.get_state(conn, "se_history_cursor"))
    if cursor is None or cursor <= FIRST_DAY:
        return f"complete - the register is loaded back to {FIRST_DAY}"
    loaded = []
    for _ in range(MONTHS_PER_RUN):
        if cursor <= FIRST_DAY:
            break
        end = cursor - timedelta(days=1)
        start = max(end.replace(day=1), FIRST_DAY)
        kept, new, updated, lines = save_export(conn, web, start, end, rates, stats, "history")
        loaded.append(f"{start:%Y-%m}: {lines:,} lines, {kept:,} kept ({new:,} new)")
        cursor = start
        database.set_state(conn, "se_history_cursor", cursor.isoformat())
    for line in loaded:
        note(line)
    if cursor <= FIRST_DAY:
        return f"loaded {len(loaded)} months; the history is complete back to {FIRST_DAY}"
    months_left = (cursor.year - FIRST_DAY.year) * 12 + cursor.month - FIRST_DAY.month
    return f"loaded {len(loaded)} months, back to {cursor}; about {months_left} months left"


# ---------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Collect Swedish executives' buys and sells "
                                                 "(Finansinspektionen's register) into the database.")
    parser.add_argument("--email", help="your contact email, sent in the User-Agent")
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS,
                        help=f"requests in this run (default {MAX_REQUESTS})")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    ran_on = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "your computer"
    now = datetime.now(timezone.utc)
    today = now.astimezone(sweden_parsing.STOCKHOLM).date()
    started = now
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "problem_page.html").unlink(missing_ok=True)

    print(f"{PROJECT} Swedish executives collector {VERSION}")
    print(f"Running on: {ran_on}")
    print(f"At most {args.max_requests} requests in this run\n")

    email = (args.email or os.environ.get("EFD_CONTACT_EMAIL") or "").strip()
    db_url = (os.environ.get("SUPABASE_DB_URL") or "").strip()
    db_password = os.environ.get("SUPABASE_DB_PASSWORD") or ""
    missing = []
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        missing.append("EFD_CONTACT_EMAIL: your contact email")
    if not db_url:
        missing.append("SUPABASE_DB_URL: Supabase's Session pooler connection string")
    else:
        try:
            database.parse_db_url(db_url, db_password)
        except database.DatabaseError as problem:
            missing.append(str(problem))
    if missing:
        print("Nothing was done, because these settings are missing or wrong:")
        for item in missing:
            print(f"  - {item}")
        print("On GitHub, add them under Settings > Secrets and variables > Actions.")
        print(f"VERDICT: {NOT_RUN} - settings missing (ran on {ran_on})")
        return 1

    log = StepLog()
    stats = {"lines": 0, "kept": 0, "new": 0, "updated": 0, "suspect": 0, "skipped_nature": 0,
             "skipped_instrument": 0, "skipped_entities": 0}
    status, failed_step, failure = OK, None, None
    conn = run_id = web = None
    step = 1
    try:
        conn = database.connect(db_url, db_password)
        database.ensure_schema(conn)
        before = database.mar_counts(conn, COUNTRY)
        run_id = database.start_run(conn, ran_on, collector="sweden")
        log.done(1, OK, f"tables ready, {before['trades']:,} Swedish trades stored so far")

        web = sweden_site.FiSession(f"{PROJECT}/{VERSION} (non-commercial public transparency project; "
                                    f"contact: {email})", args.max_requests)
        step = 2
        rates, detail = update_rates(conn, web, today)
        log.done(2, OK, detail)

        step = 3
        log.done(3, OK, web.robots_allow())

        step = 4
        log.done(4, OK, export_recent(conn, web, rates, stats, today))

        step = 5
        log.done(5, OK, load_history(conn, web, rates, stats))

    except sweden_site.SiteStop as stop:
        status, failed_step, failure = stop.status, step, stop.detail
        log.done(step, status, stop.detail, sweden_site.http_codes(stop.response))
        log.rest(step, NOT_RUN)
        if stop.response is not None:
            (OUTPUT_DIR / "problem_page.html").write_bytes(stop.response.content[:2_000_000])
    except sweden_site.OutOfRequests:
        log.done(step, OK, "this run's request budget is used up")
        log.rest(step, "SKIPPED")
    except database.DatabaseError as error:
        status, failed_step, failure = DB_ERROR, step, str(error)
        log.done(step, DB_ERROR, failure)
        log.rest(step, NOT_RUN)
    except Exception as error:
        status, failed_step, failure = ERROR, step, f"{type(error).__name__}: {error}"
        log.done(step, ERROR, failure)
        log.rest(step, NOT_RUN)
        print("\nTechnical details (useful for fixing the collector):")
        traceback.print_exc(file=sys.stdout)

    totals = None
    if conn is not None and status != DB_ERROR:
        try:
            totals = database.mar_counts(conn, COUNTRY)
        except database.DatabaseError:
            pass
    if failure is not None:
        outcome = f"at step {failed_step} ({STEP_NAMES[failed_step]}) - {failure}"
    elif stats["kept"] == 0 and stats["lines"] == 0:
        outcome = "- nothing new, everything is up to date"
    else:
        outcome = (f"- {plural(stats['kept'], 'trade')} saved from {stats['lines']:,} register lines "
                   f"({stats['new']:,} new, {stats['updated']:,} seen before)")
    verdict_line = f"VERDICT: {status} {outcome} (ran on {ran_on})"

    print()
    if stats["lines"]:
        print(f"Left out this run: {stats['skipped_nature']:,} lines that aren't buys or sells (subscriptions, "
              f"option exercises, gifts...), {stats['skipped_instrument']:,} in other instruments (warrants, bonds...), "
              f"{stats['skipped_entities']:,} reported for companies or funds rather than people")
    if totals:
        first = totals["first_published"]
        last = totals["last_published"]
        span = f"published {str(first)[:10]} to {str(last)[:10]}" if first and last else "nothing published yet"
        print(f"Database now: {totals['trades']:,} Swedish trades ({totals['buys']:,} buys, {totals['sells']:,} sells, "
              f"{totals['suspect']:,} suspect, {totals['not_current']:,} revised or cancelled) by {totals['people']:,} "
              f"people at {totals['companies']:,} companies, {span}; table uses {totals['size']}")
    if web is not None:
        print(f"Requests in this run: {web.requests_sent}")
    if status in ADVICE:
        print("\n".join(ADVICE[status]))
    if conn is not None and run_id is not None:
        try:
            database.finish_run(conn, run_id, status, verdict_line, {
                "collector": "sweden", "started_at_utc": started.isoformat(timespec="seconds"),
                "steps": log.steps, "stats": stats, "totals": totals,
                "requests": web.requests_sent if web else 0,
            })
        except database.DatabaseError:
            pass
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    print(verdict_line)
    return 0 if status == OK else 1


if __name__ == "__main__":
    sys.exit(main())
