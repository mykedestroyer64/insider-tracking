"""
collect_house.py - the House collector: saves House members' trade reports in the database.

GitHub Actions runs this every 3 hours (see .github/workflows/collect-house.yml).
Each run:
  1. connects to the Supabase database (and adds what the House needs, the first time)
  2. checks this year's and last year's lists of filings for new trade reports
  3. lists older years, three per run, back to 2013
  4. reads electronic reports that haven't been read yet (PDF files), newest
     first, and saves their transactions
Scanned paper reports are stored with status "not_read" and their link.

It sends at most 200 requests to the House site per run, 1.5-2.5 seconds
apart, so the history fills in over a few days. Everything is saved as it
goes: a run that stops early loses nothing, and the next run carries on.

Settings: the same three GitHub secrets as the Senate collector
(EFD_CONTACT_EMAIL, SUPABASE_DB_URL, SUPABASE_DB_PASSWORD).
Reading the PDFs needs the pdfplumber library (it is in requirements.txt).

The last line printed is the verdict. Exit code: 0 if OK, 1 otherwise.
"""

import argparse
import os
import re
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

import database
import house_parsing
import house_site
from house_site import BLOCKED, CHANGED, OK, SITE_DOWN

PROJECT = "insider-tracking"
VERSION = "0.3"

MAX_REQUESTS = 200          # requests to the House site per run
HISTORY_START_YEAR = 2013   # the first year with trade reports in the Clerk's lists
YEARS_PER_RUN = 3           # older yearly lists to read per run
FAILURES_BEFORE_STOP = 3    # unreadable reports in a row before we assume the site changed

DB_ERROR, ERROR, NOT_RUN = "DB_ERROR", "ERROR", "NOT_RUN"

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

STEP_NAMES = {
    1: "Connect to the database",
    2: "Check for new reports",
    3: "List older years (history)",
    4: "Read reports",
}
TOTAL_STEPS = len(STEP_NAMES)

ADVICE = {
    BLOCKED: ["The House site refused the collector. The next scheduled run tries again in",
              "3 hours. If several runs in a row are BLOCKED, stop there and share this result."],
    SITE_DOWN: ["The House site had a server error. The next scheduled run tries again in 3 hours."],
    CHANGED: ["A file did not look the way the collector expects, so the House site may work",
              "differently now. Share this log so the collector can be updated."],
    DB_ERROR: ["Check the SUPABASE_DB_URL secret (it must be Supabase's Session pooler",
               "string), the SUPABASE_DB_PASSWORD secret, and that the Supabase project",
               "isn't paused."],
    ERROR: ["The collector hit a problem it didn't expect. Share this log."],
}


class StepLog:
    """Prints one line per finished step and keeps them for the run summary."""

    def __init__(self):
        self.steps = []

    def done(self, number, status, detail="", codes=()):
        codes = list(codes)
        http = f" (HTTP {' -> '.join(str(code) for code in codes)})" if codes else ""
        if detail.startswith("HTTP "):
            http = ""
        label = f"{STEP_NAMES[number]} ".ljust(38, ".")
        print(f"[{number}/{TOTAL_STEPS}] {label} {status}{http}" + (f" - {detail}" if detail else ""),
              flush=True)
        self.steps.append({"step": number, "name": STEP_NAMES[number], "status": status,
                           "http_codes": codes, "detail": detail})

    def rest(self, after, status):
        for number in range(after + 1, TOTAL_STEPS + 1):
            self.done(number, status)


def note(message):
    print(f"      {message}", flush=True)


def plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def kinds_text(reports):
    kinds = [report["kind"] for report in reports]
    return f"{kinds.count('electronic')} electronic, {kinds.count('paper')} scanned"


def read_list(response, year):
    """The trade reports in a downloaded yearly list (stops the run if it can't be read)."""
    try:
        return house_parsing.parse_list(response.content, house_site.REPORT_URL)
    except house_parsing.ReportProblem as problem:
        raise house_site.unexpected(response, f"the {year} list of filings: {problem}") from None


# --------------------------------------------------------------------- steps

def check_recent(conn, web, today, stats):
    """Step 2: this year's and last year's lists (filings early in a year can be in the last one)."""
    parts = []
    for year in (today.year, today.year - 1):
        etag = database.get_state(conn, f"house_list_etag_{year}")
        modified = database.get_state(conn, f"house_list_modified_{year}")
        response, status = web.get_list(year, etag, modified)
        if status != "new":
            parts.append(f"{year}: {'no list yet' if status == 'missing' else 'unchanged'}")
            continue
        reports = read_list(response, year)
        new = database.add_reports(conn, reports)
        stats["new_reports"] += len(new)
        for key, header in (("etag", "ETag"), ("modified", "Last-Modified")):
            if response.headers.get(header):
                database.set_state(conn, f"house_list_{key}_{year}", response.headers[header])
        part = f"{year}: {plural(len(reports), 'trade report')}, {len(new)} new"
        if new:
            part += f" ({kinds_text(new)})"
        parts.append(part)
    if database.get_state(conn, "house_history_listed_back_to") is None:
        database.set_state(conn, "house_history_listed_back_to", str(today.year - 1))
    return "; ".join(parts)


def list_history(conn, web, stats):
    """Step 3: the lists of older years, YEARS_PER_RUN per run, back to HISTORY_START_YEAR."""
    back_to = int(database.get_state(conn, "house_history_listed_back_to") or 0)
    if not back_to:
        return "nothing to do yet - the check for new reports hasn't finished"
    if back_to <= HISTORY_START_YEAR:
        return f"complete - every list back to {HISTORY_START_YEAR} is read"
    new_reports, years = [], []
    try:
        while back_to > HISTORY_START_YEAR and len(years) < YEARS_PER_RUN:
            year = back_to - 1
            response, status = web.get_list(year)
            if status == "new":
                new_reports += database.add_reports(conn, read_list(response, year))
            else:
                note(f"no list for {year}")
            back_to = year
            database.set_state(conn, "house_history_listed_back_to", str(back_to))
            years.append(year)
    except house_site.OutOfRequests:
        note("stopped listing: this run's request budget is used up")
    stats["new_reports"] += len(new_reports)
    if back_to <= HISTORY_START_YEAR:
        return (f"complete - every list back to {HISTORY_START_YEAR} is read "
                f"({len(new_reports)} new this run)")
    return (f"read the lists for {', '.join(str(y) for y in years) or 'no year'}: "
            f"{plural(len(new_reports), 'new report')} ({kinds_text(new_reports)})")


def read_reports(conn, web, stats):
    """Step 4: download and read electronic reports, newest first, within the budget."""
    if web.requests_left == 0:
        left = database.counts(conn, "house")["to_read"]
        return f"no requests left in this run's budget; {left} left to read"
    queue = database.reports_to_read(conn, limit=web.requests_left, chamber="house")
    if not queue:
        return "nothing to read - every electronic report found so far is read"
    note(f"reading up to {len(queue)} reports, newest first")
    in_a_row = 0
    for number, report in enumerate(queue, 1):
        try:
            response = web.get_report(report["url"])
        except house_site.OutOfRequests:
            break
        path, problem = report["report_path"], None
        if response.status_code in (404, 410):
            problem = f"file not found (HTTP {response.status_code})"
            database.mark_failed(conn, path, problem)
        elif not response.content.startswith(b"%PDF"):
            problem = "the file is not a PDF"
            database.mark_failed(conn, path, problem)
        else:
            try:
                transactions, expected = house_parsing.parse_report(response.content)
            except house_parsing.ReportProblem as error:
                if "no text" in str(error):     # a scan with an electronic-looking number
                    database.mark_scanned(conn, path, str(error))
                    stats["scanned"] += 1
                else:
                    problem = str(error)
                    database.mark_failed(conn, path, problem)
            else:
                remark = None
                if len(transactions) < expected:
                    remark = (f"{len(transactions)} of about {expected} transactions could be read")
                database.save_report(conn, path, transactions, None, remark,
                                     is_amendment=house_parsing.is_amendment(transactions))
                stats["reports_read"] += 1
                stats["transactions_saved"] += len(transactions)
        if problem:
            stats["reports_failed"] += 1
            in_a_row += 1
            note(f"could not read {report['first_name']} {report['last_name']} "
                 f"({report['date_received']}): {problem}")
            if in_a_row >= FAILURES_BEFORE_STOP:
                raise house_site.SiteStop(CHANGED, f"{in_a_row} reports in a row could not be "
                                                   f"read (last: {problem})", response)
        else:
            in_a_row = 0
        if number % 25 == 0:
            note(f"{number} of {len(queue)} done")

    left = database.counts(conn, "house")["to_read"]
    detail = (f"read {plural(stats['reports_read'], 'report')}, "
              f"{plural(stats['transactions_saved'], 'transaction')} saved")
    if stats["scanned"]:
        detail += f", {stats['scanned']} turned out to be scans"
    if stats["reports_failed"]:
        detail += f", {stats['reports_failed']} could not be read"
    return detail + f"; {left} left to read"


# ---------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Collect US House periodic transaction "
                                                 "reports into the database.")
    parser.add_argument("--email", help="your contact email, sent in the User-Agent")
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS,
                        help=f"requests to the House site in this run (default {MAX_REQUESTS})")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    ran_on = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "your computer"
    today = date.today()
    started = datetime.now(timezone.utc)
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "problem_page.html").unlink(missing_ok=True)

    print(f"{PROJECT} House collector {VERSION}")
    print(f"Running on: {ran_on}")
    print(f"At most {args.max_requests} requests to the House site in this run\n")

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
    stats = {"new_reports": 0, "reports_read": 0, "reports_failed": 0, "scanned": 0,
             "transactions_saved": 0}
    status, failed_step, failure = OK, None, None
    conn = run_id = web = None
    step = 1
    try:
        conn = database.connect(db_url, db_password)
        database.ensure_schema(conn)
        before = database.counts(conn, "house")
        run_id = database.start_run(conn, ran_on, collector="house")
        log.done(1, OK, f"tables ready, {before['reports']} House reports stored so far")

        web = house_site.HouseSession(f"{PROJECT}/{VERSION} (non-commercial public transparency "
                                      f"project; contact: {email})", args.max_requests)
        step = 2
        log.done(2, OK, check_recent(conn, web, today, stats))

        step = 3
        log.done(3, OK, list_history(conn, web, stats))

        step = 4
        log.done(4, OK, read_reports(conn, web, stats))

    except house_site.SiteStop as stop:
        status, failed_step, failure = stop.status, step, stop.detail
        log.done(step, status, stop.detail, house_site.http_codes(stop.response))
        log.rest(step, NOT_RUN)
        if stop.response is not None:
            (OUTPUT_DIR / "problem_page.html").write_bytes(stop.response.content[:2_000_000])
    except house_site.OutOfRequests:
        log.done(step, OK, "this run's request budget is used up")
        log.rest(step, "SKIPPED")
    except database.DatabaseError as error:
        status, failed_step, failure = DB_ERROR, step, str(error)
        log.done(step, DB_ERROR, failure)
        log.rest(step, NOT_RUN)
    except Exception as error:   # a bug, or something nobody expected
        status, failed_step, failure = ERROR, step, f"{type(error).__name__}: {error}"
        log.done(step, ERROR, failure)
        log.rest(step, NOT_RUN)
        print("\nTechnical details (useful for fixing the collector):")
        traceback.print_exc(file=sys.stdout)

    totals, history = None, None
    if conn is not None and status != DB_ERROR:
        try:
            totals = database.counts(conn, "house")
            history = database.get_state(conn, "house_history_listed_back_to")
        except database.DatabaseError:
            pass
    history_year = int(history) if history else None
    history_complete = history_year is not None and history_year <= HISTORY_START_YEAR

    if failure is not None:
        outcome = f"at step {failed_step} ({STEP_NAMES[failed_step]}) - {failure}"
    elif (stats["new_reports"] == 0 and stats["reports_read"] == 0 and totals
          and totals["to_read"] == 0 and history_complete):
        outcome = "- no new reports, everything is up to date"
    else:
        outcome = (f"- {plural(stats['new_reports'], 'new report')}, "
                   f"{stats['reports_read']} read "
                   f"({plural(stats['transactions_saved'], 'transaction')} saved)")
        if totals:
            outcome += f", {totals['to_read']} left to read"
    verdict_line = f"VERDICT: {status} {outcome} (ran on {ran_on})"

    print()
    if totals:
        print(f"Database now: {totals['reports']} House reports ({totals['read']} read, "
              f"{totals['to_read']} to read, {totals['paper_not_read']} scanned not read, "
              f"{totals['failed']} failed), {totals['transactions']} transactions")
    if history_complete:
        print(f"History: complete back to {HISTORY_START_YEAR}")
    elif history_year:
        print(f"History: lists read back to {history_year} (they go back to {HISTORY_START_YEAR})")
    if web is not None:
        print(f"Requests to the House site in this run: {web.requests_sent}")
    if status in ADVICE:
        print("\n".join(ADVICE[status]))
    if conn is not None and run_id is not None:
        try:
            database.finish_run(conn, run_id, status, verdict_line, {
                "collector": "house",
                "started_at_utc": started.isoformat(timespec="seconds"),
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
