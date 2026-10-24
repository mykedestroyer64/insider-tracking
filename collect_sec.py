"""
collect_sec.py - the company-insider collector: officers' and directors' buys and sells (SEC Form 4).

GitHub Actions runs this every 3 hours (see .github/workflows/collect-sec.yml).
Each run:
  1. connects to the Supabase database (and adds the insider tables the first time)
  2. loads the SEC's quarterly data files, newest first, two per run, back to 2021
     (each file holds every Form 4 of a quarter; only officers' and directors'
     buys and sells are kept)
  3. reads EDGAR's daily lists of filings for the days after the newest quarterly
     file, downloads each new Form 4 and keeps the same kind of trades
Filings seen before are updated, never duplicated, so the two sources can overlap.

It sends at most 1,200 requests to the SEC per run, 0.3-0.6 seconds apart,
with a User-Agent that names the project and gives a contact email, as the
SEC asks. Everything is saved as it goes.

Settings: the same three GitHub secrets as the other collectors
(EFD_CONTACT_EMAIL, SUPABASE_DB_URL, SUPABASE_DB_PASSWORD).

The last line printed is the verdict. Exit code: 0 if OK, 1 otherwise.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import database
import sec_parsing
import sec_site
from sec_site import BLOCKED, CHANGED, OK, SITE_DOWN

PROJECT = "insider-tracking"
VERSION = "0.4"

MAX_REQUESTS = 1200        # requests to the SEC per run
FIRST_QUARTER = (2021, 1)  # how far back the quarterly files are loaded
QUARTERS_PER_RUN = 2
DAY_COMPLETE_AFTER = timedelta(hours=6)   # a day's list is final 6 hours after that day ends (UTC), i.e. after 1 am in Washington
FAILURES_BEFORE_STOP = 5   # unreadable filings in a row before we assume EDGAR changed

DB_ERROR, ERROR, NOT_RUN = "DB_ERROR", "ERROR", "NOT_RUN"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

STEP_NAMES = {
    1: "Connect to the database",
    2: "Load quarterly files (history)",
    3: "Read new filings (daily lists)",
}
TOTAL_STEPS = len(STEP_NAMES)

ADVICE = {
    BLOCKED: ["The SEC refused the collector. It asks for a User-Agent with a name and an email and",
              "fewer than 10 requests a second, which this collector does. The next scheduled run",
              "tries again in 3 hours; if several runs in a row are BLOCKED, stop and share this result."],
    SITE_DOWN: ["The SEC's site had a server error. The next scheduled run tries again in 3 hours."],
    CHANGED: ["A page or file did not look the way the collector expects, so the SEC's site may",
              "work differently now. Share this log so the collector can be updated."],
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
        label = f"{STEP_NAMES[number]} ".ljust(38, ".")
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


def quarter_of(day):
    return day.year, (day.month - 1) // 3 + 1


def quarter_end(year, quarter):
    month = quarter * 3
    first_of_next = date(year + (month == 12), (month % 12) + 1, 1)
    return first_of_next - timedelta(days=1)


def quarter_name(year, quarter):
    return f"{year}q{quarter}"


# --------------------------------------------------------------------- steps

def find_quarterly_files(web):
    """The quarterly files listed on the SEC's page: {(year, quarter): url}."""
    response = web.get(sec_site.DATASETS_PAGE)
    links = {}
    for href, year, quarter in re.findall(r'href="([^"]*?(\d{4})q([1-4])_form345\.zip)"', response.text, re.I):
        links[(int(year), int(quarter))] = href if href.startswith("http") else sec_site.SEC_URL + href
    if not links:
        raise sec_site.unexpected(response, "no links to quarterly files (..._form345.zip) on the data sets page")
    return links


def load_quarters(conn, web, files, stats):
    """Step 2: load the quarterly files not loaded yet, newest first, QUARTERS_PER_RUN per run."""
    done = set(filter(None, (database.get_state(conn, "sec_quarters_done") or "").split(",")))
    wanted = sorted((q for q in files if q >= FIRST_QUARTER and quarter_name(*q) not in done), reverse=True)
    if not wanted:
        return f"complete - every quarterly file from {quarter_name(*FIRST_QUARTER)} to {quarter_name(*max(files))} is loaded"
    loaded = []
    for year, quarter in wanted[:QUARTERS_PER_RUN]:
        name = quarter_name(year, quarter)
        with tempfile.TemporaryDirectory() as folder:
            response = web.get(files[(year, quarter)])
            if not response.content.startswith(b"PK"):
                raise sec_site.unexpected(response, f"the {name} file is not a ZIP file")
            path = Path(folder) / f"{name}.zip"
            path.write_bytes(response.content)
            try:
                filings, trades, counts = sec_parsing.parse_quarter(path)
            except (sec_parsing.FilingProblem, OSError) as problem:
                raise sec_site.SiteStop(CHANGED, f"the {name} file could not be read: {problem}") from None
        new = database.save_insider_filings(conn, filings, trades, source=name)
        done.add(name)
        database.set_state(conn, "sec_quarters_done", ",".join(sorted(done)))
        stats["filings_saved"] += len(filings)
        stats["trades_saved"] += len(trades)
        stats["suspect"] += counts["suspect"]
        loaded.append(f"{name}: {len(response.content) / 1e6:.0f} MB, {counts['form4']:,} Form 4s, kept "
                      f"{plural(len(trades), 'trade')} by officers and directors in {len(filings):,} filings "
                      f"({new:,} new)")
    for line in loaded:
        note(line)
    left = len(wanted) - len(loaded)
    return f"loaded {len(loaded)}; {left} quarterly file{'s' if left != 1 else ''} left to load"


def read_daily(conn, web, files, stats, now):
    """Step 3: new filings from EDGAR's daily lists, for the days after the newest quarterly file."""
    newest = max(files)
    first_day = quarter_end(*newest) + timedelta(days=1)
    cursor = database.get_state(conn, "sec_daily_cursor")        # the last day fully processed
    day = max(first_day, date.fromisoformat(cursor) + timedelta(days=1)) if cursor else first_day
    today = (now - DAY_COMPLETE_AFTER).date() - timedelta(days=1)   # the newest day whose list is final
    if day > now.date():
        return f"up to date - the daily lists after {quarter_name(*newest)} are read through {cursor}"
    seen_state = json.loads(database.get_state(conn, "sec_daily_seen") or "{}")
    days_done, fetched, kept, failures_in_a_row = 0, 0, 0, 0
    try:
        while day <= now.date():
            if day.weekday() >= 5:                                # EDGAR publishes no lists at weekends
                if day <= today:
                    database.set_state(conn, "sec_daily_cursor", day.isoformat())
                day += timedelta(days=1)
                continue
            year, quarter = quarter_of(day)
            response = web.get(sec_site.DAILY_INDEX.format(year=year, quarter=quarter, day=day), ok_codes=(404,))
            if response.status_code == 404:                       # a holiday, or not published yet
                if day <= today:
                    database.set_state(conn, "sec_daily_cursor", day.isoformat())
                    day += timedelta(days=1)
                    continue
                break
            listed = sec_parsing.parse_daily_index(response.text)
            if not listed and "Form Type" not in response.text:
                raise sec_site.unexpected(response, f"the daily list for {day} could not be read")
            seen = set(seen_state.get("accessions", [])) if seen_state.get("day") == day.isoformat() else set()
            todo = [f for f in listed if f["accession"] not in seen]
            for filing in todo:
                try:
                    page = web.get(sec_site.ARCHIVES + filing["path"], ok_codes=(404,))
                except sec_site.OutOfRequests:
                    raise
                fetched += 1
                if page.status_code == 404:
                    note(f"filing {filing['accession']} is listed but not available (HTTP 404)")
                else:
                    try:
                        parsed, trades = sec_parsing.parse_filing(page.text, filing["accession"], filing["filed"])
                    except sec_parsing.FilingProblem as problem:
                        failures_in_a_row += 1
                        note(f"could not read filing {filing['accession']}: {problem}")
                        if failures_in_a_row >= FAILURES_BEFORE_STOP:
                            raise sec_site.SiteStop(CHANGED, f"{failures_in_a_row} filings in a row could not be "
                                                             f"read (last: {problem})", page)
                    else:
                        failures_in_a_row = 0
                        if trades:
                            database.save_insider_filings(conn, [parsed], trades, source=f"daily {day}")
                            kept += 1
                            stats["filings_saved"] += 1
                            stats["trades_saved"] += len(trades)
                            stats["suspect"] += sum(1 for t in trades if t["suspect"])
                seen.add(filing["accession"])
                seen_state = {"day": day.isoformat(), "accessions": sorted(seen)}
                if len(seen) % 25 == 0:
                    database.set_state(conn, "sec_daily_seen", json.dumps(seen_state))
            database.set_state(conn, "sec_daily_seen", json.dumps(seen_state))
            if day <= today:
                database.set_state(conn, "sec_daily_cursor", day.isoformat())
                database.set_state(conn, "sec_daily_seen", "{}")
                days_done += 1
                day += timedelta(days=1)
            else:
                break                                             # today's list isn't final: re-read it next run
    except sec_site.OutOfRequests:
        note("stopped: this run's request budget is used up")
        database.set_state(conn, "sec_daily_seen", json.dumps(seen_state))
    stats["filings_fetched"] += fetched
    cursor = database.get_state(conn, "sec_daily_cursor") or "none yet"
    return (f"{plural(days_done, 'day')} finished (through {cursor}); {fetched:,} Form 4s read, "
            f"{kept:,} with officers' or directors' buys or sells")


# ---------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Collect company insiders' buys and sells (SEC Form 4) "
                                                 "into the database.")
    parser.add_argument("--email", help="your contact email, sent in the User-Agent")
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS,
                        help=f"requests to the SEC in this run (default {MAX_REQUESTS})")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    ran_on = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "your computer"
    now = datetime.now(timezone.utc)
    started = now
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "problem_page.html").unlink(missing_ok=True)

    print(f"{PROJECT} company-insider collector {VERSION}")
    print(f"Running on: {ran_on}")
    print(f"At most {args.max_requests} requests to the SEC in this run\n")

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
    stats = {"filings_saved": 0, "trades_saved": 0, "filings_fetched": 0, "suspect": 0}
    status, failed_step, failure = OK, None, None
    conn = run_id = web = None
    step = 1
    try:
        conn = database.connect(db_url, db_password)
        database.ensure_schema(conn)
        before = database.insider_counts(conn)
        run_id = database.start_run(conn, ran_on, collector="sec")
        log.done(1, OK, f"tables ready, {before['trades']:,} insider trades stored so far")

        web = sec_site.SecSession(f"{PROJECT}/{VERSION} (non-commercial public transparency project; "
                                  f"contact: {email})", args.max_requests)
        step = 2
        files = find_quarterly_files(web)
        log.done(2, OK, load_quarters(conn, web, files, stats))

        step = 3
        log.done(3, OK, read_daily(conn, web, files, stats, now))

    except sec_site.SiteStop as stop:
        status, failed_step, failure = stop.status, step, stop.detail
        log.done(step, status, stop.detail, sec_site.http_codes(stop.response))
        log.rest(step, NOT_RUN)
        if stop.response is not None:
            (OUTPUT_DIR / "problem_page.html").write_bytes(stop.response.content[:2_000_000])
    except sec_site.OutOfRequests:
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
            totals = database.insider_counts(conn)
        except database.DatabaseError:
            pass
    if failure is not None:
        outcome = f"at step {failed_step} ({STEP_NAMES[failed_step]}) - {failure}"
    elif stats["filings_saved"] == 0 and stats["filings_fetched"] == 0:
        outcome = "- nothing new, everything is up to date"
    else:
        outcome = (f"- {plural(stats['trades_saved'], 'trade')} saved from "
                   f"{plural(stats['filings_saved'], 'filing')}")
        if stats["filings_fetched"]:
            outcome += f", {stats['filings_fetched']:,} new filings read"
    verdict_line = f"VERDICT: {status} {outcome} (ran on {ran_on})"

    print()
    if totals:
        print(f"Database now: {totals['trades']:,} insider trades ({totals['buys']:,} buys, {totals['sells']:,} sells, "
              f"{totals['suspect']:,} suspect) from {totals['filings']:,} filings by {totals['insiders']:,} insiders, "
              f"filed {totals['first_filing']} to {totals['last_filing']}; tables use {totals['size']}")
    if web is not None:
        print(f"Requests to the SEC in this run: {web.requests_sent}")
    if status in ADVICE:
        print("\n".join(ADVICE[status]))
    if conn is not None and run_id is not None:
        try:
            database.finish_run(conn, run_id, status, verdict_line, {
                "collector": "sec", "started_at_utc": started.isoformat(timespec="seconds"),
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
