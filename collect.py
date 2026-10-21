"""
collect.py - step 2 of the insider-tracking project: the collector.

GitHub Actions runs this every 3 hours (see .github/workflows/collect.yml).
Each run:
  1. connects to the Supabase database (and creates the tables the first time)
  2. opens eFD and accepts its usage agreement
  3. looks for reports received in the last 60 days and stores the new ones
  4. lists older reports, two years per run, back to January 2012
  5. reads electronic reports that haven't been read yet, newest first, and
     saves their transactions
Scanned paper reports are stored with status "not_read" and their link.

It sends at most 200 requests to eFD per run, 2-3 seconds apart, so the full
history fills in over several days. Everything is saved as it goes: a run
that stops early loses nothing, and the next run carries on.

Settings (GitHub secrets, passed in as environment variables):
  EFD_CONTACT_EMAIL     your email, sent in the User-Agent (or use --email)
  SUPABASE_DB_URL       Supabase's "Session pooler" connection string
  SUPABASE_DB_PASSWORD  your Supabase database password

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
import efd_site
import parsing
from efd_site import BLOCKED, CHANGED, OK, SITE_DOWN

PROJECT = "insider-tracking"
VERSION = "0.2"

MAX_REQUESTS = 200          # requests to eFD per run
RECENT_DAYS = 60            # every run re-checks reports received in the last 60 days
HISTORY_START = date(2012, 1, 1)
MONTHS_PER_RUN = 24         # months of history to list per run
FAILURES_BEFORE_STOP = 3    # unreadable reports in a row before we assume the site changed

DB_ERROR, ERROR, NOT_RUN = "DB_ERROR", "ERROR", "NOT_RUN"

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

STEP_NAMES = {
    1: "Connect to the database",
    2: "Open eFD and accept the agreement",
    3: "Check for new reports",
    4: "List older reports (history)",
    5: "Read reports",
}

ADVICE = {
    BLOCKED: ["eFD refused the collector. The next scheduled run tries again in 3 hours.",
              "If several runs in a row are BLOCKED, stop there and share this result."],
    SITE_DOWN: ["eFD had a server error or showed a maintenance page.",
                "The next scheduled run tries again in 3 hours."],
    CHANGED: ["A page did not look the way the collector expects, so eFD may work",
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
        print(f"[{number}/5] {label} {status}{http}" + (f" - {detail}" if detail else ""),
              flush=True)
        self.steps.append({"step": number, "name": STEP_NAMES[number], "status": status,
                           "http_codes": codes, "detail": detail})

    def rest(self, after, status):
        """Give every step after `after` the same status (NOT_RUN or SKIPPED)."""
        for number in range(after + 1, 6):
            self.done(number, status)


def note(message):
    """A progress line printed while a step is still working."""
    print(f"      {message}", flush=True)


def iso_date(text):
    return date.fromisoformat(text) if text else None


def month_start_before(day):
    """The first day of the month that comes before `day` (or of its own month)."""
    start = day.replace(day=1)
    if start == day:
        start = (day - timedelta(days=1)).replace(day=1)
    return max(start, HISTORY_START)


def read_rows(rows):
    """Turn search result rows into report dicts (each report once)."""
    reports = {}
    for row in rows:
        report = parsing.parse_search_row(row, efd_site.BASE_URL)
        if report is None:
            raise efd_site.EfdStop(CHANGED, "a search result row does not have the expected "
                                            "columns (first name, last name, filer, report "
                                            "link, date)")
        reports.setdefault(report["report_path"], report)
    return list(reports.values())


def plural(count, word):
    """"1 report", "2 reports"."""
    return f"{count} {word}" + ("" if count == 1 else "s")


def kinds_text(reports):
    kinds = [report["kind"] for report in reports]
    text = f"{kinds.count('electronic')} electronic, {kinds.count('paper')} paper"
    if len(kinds) > kinds.count("electronic") + kinds.count("paper"):
        text += f", {len(kinds) - kinds.count('electronic') - kinds.count('paper')} other"
    return text


# --------------------------------------------------------------------- steps

def check_recent(conn, web, today, stats):
    """Step 3: reports received in the last RECENT_DAYS days (more after a long pause)."""
    last_scan = iso_date(database.get_state(conn, "recent_scan_day"))
    recent_from = min(last_scan or today, today) - timedelta(days=RECENT_DAYS)
    reports = read_rows(web.search_all(recent_from, today + timedelta(days=1)))
    new = database.add_reports(conn, reports)
    database.set_state(conn, "recent_scan_day", today.isoformat())
    if database.get_state(conn, "history_listed_back_to") is None:
        database.set_state(conn, "history_listed_back_to", recent_from.isoformat())
    stats["new_reports"] += len(new)
    detail = (f"{plural(len(reports), 'report')} received since {recent_from:%m/%d/%Y}, "
              f"{len(new)} new")
    if new:
        detail += f" ({kinds_text(new)})"
    return detail


def list_history(conn, web, stats):
    """Step 4: list older reports month by month, going back to HISTORY_START."""
    cursor = iso_date(database.get_state(conn, "history_listed_back_to"))
    if cursor is None:
        return "nothing to do yet - the first check for new reports hasn't finished"
    if cursor <= HISTORY_START:
        return f"complete - every report back to {HISTORY_START:%m/%Y} is listed"
    new_reports, months = [], 0
    try:
        while cursor > HISTORY_START and months < MONTHS_PER_RUN:
            start = month_start_before(cursor)
            reports = read_rows(web.search_all(start, cursor))
            new_reports += database.add_reports(conn, reports)
            cursor = start
            database.set_state(conn, "history_listed_back_to", cursor.isoformat())
            months += 1
    except efd_site.OutOfRequests:
        note("stopped listing: this run's request budget is used up")
    stats["new_reports"] += len(new_reports)
    if cursor <= HISTORY_START:
        return f"complete - listed back to {HISTORY_START:%m/%Y} ({len(new_reports)} new this run)"
    return (f"listed {plural(months, 'more month')}, back to {cursor:%m/%Y}: "
            f"{plural(len(new_reports), 'new report')} ({kinds_text(new_reports)})")


def read_reports(conn, web, stats):
    """Step 5: download and read electronic reports, newest first, within the budget."""
    if web.requests_left == 0:
        left = database.counts(conn)["to_read"]
        return f"no requests left in this run's budget; {left} left to read"
    queue = database.reports_to_read(conn, limit=web.requests_left)
    if not queue:
        return "nothing to read - every electronic report found so far is read"
    note(f"reading up to {len(queue)} reports, newest first")
    in_a_row = 0
    for number, report in enumerate(queue, 1):
        try:
            response = web.get_report(report["url"])
        except efd_site.OutOfRequests:
            break
        path = report["report_path"]
        problem = None
        if response.status_code in (404, 410):
            problem = f"page not found (HTTP {response.status_code})"
            database.mark_failed(conn, path, problem)
        else:
            try:
                _, transactions, odd_rows = parsing.parse_report_page(response.text)
            except parsing.ReportProblem as error:
                problem = str(error)
                database.mark_failed(conn, path, problem, response.content)
            else:
                remark = None
                if odd_rows:
                    remark = (f"{odd_rows} table rows had a different number of cells and "
                              "were skipped")
                database.save_report(conn, path, transactions, response.content, remark)
                stats["reports_read"] += 1
                stats["transactions_saved"] += len(transactions)
        if problem:
            stats["reports_failed"] += 1
            in_a_row += 1
            note(f"could not read {report['first_name']} {report['last_name']} "
                 f"({report['date_received']}): {problem}")
            if in_a_row >= FAILURES_BEFORE_STOP:
                raise efd_site.EfdStop(CHANGED, f"{in_a_row} reports in a row could not be read "
                                                f"(last: {problem})", response)
        else:
            in_a_row = 0
        if number % 25 == 0:
            note(f"{number} of {len(queue)} done")

    left = database.counts(conn)["to_read"]
    detail = (f"read {plural(stats['reports_read'], 'report')}, "
              f"{plural(stats['transactions_saved'], 'transaction')} saved")
    if stats["reports_failed"]:
        detail += f", {stats['reports_failed']} could not be read"
    return detail + f"; {left} left to read"


# ---------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Collect US Senate periodic transaction "
                                                 "reports from eFD into the database.")
    parser.add_argument("--email", help="your contact email, sent in the User-Agent")
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS,
                        help=f"requests to eFD in this run (default {MAX_REQUESTS})")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")   # never crash on an unusual character
    except AttributeError:
        pass

    ran_on = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "your computer"
    today = date.today()
    started = datetime.now(timezone.utc)
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "problem_page.html").unlink(missing_ok=True)

    print(f"{PROJECT} collector {VERSION}")
    print(f"Running on: {ran_on}")
    print(f"At most {args.max_requests} requests to eFD in this run, 2-3 seconds apart\n")

    # Check the settings before doing anything
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
    stats = {"new_reports": 0, "reports_read": 0, "reports_failed": 0, "transactions_saved": 0}
    status, failed_step, failure = OK, None, None
    conn = run_id = web = None
    step = 1
    try:
        conn = database.connect(db_url, db_password)
        database.ensure_schema(conn)
        before = database.counts(conn)
        run_id = database.start_run(conn, ran_on)
        log.done(1, OK, f"tables ready, {before['reports']} reports stored so far")

        step = 2
        web = efd_site.EfdSession(f"{PROJECT}/{VERSION} (non-commercial public transparency "
                                  f"project; contact: {email})", args.max_requests)
        log.done(2, OK, "agreement accepted", web.open())

        step = 3
        log.done(3, OK, check_recent(conn, web, today, stats))

        step = 4
        log.done(4, OK, list_history(conn, web, stats))

        step = 5
        log.done(5, OK, read_reports(conn, web, stats))

    except efd_site.EfdStop as stop:
        status, failed_step, failure = stop.status, step, stop.detail
        log.done(step, status, stop.detail, efd_site.http_codes(stop.response))
        log.rest(step, NOT_RUN)
        if stop.response is not None:
            (OUTPUT_DIR / "problem_page.html").write_bytes(stop.response.content)
    except efd_site.OutOfRequests:
        # Only possible in steps 2-3, with a tiny budget: nothing is wrong, the run just ends
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

    # Totals and the run's record in the database
    totals, history = None, None
    if conn is not None and status != DB_ERROR:
        try:
            totals = database.counts(conn)
            history = database.get_state(conn, "history_listed_back_to")
        except database.DatabaseError:
            pass

    history_day = date.fromisoformat(history) if history else None
    history_complete = history_day is not None and history_day <= HISTORY_START
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
        print(f"Database now: {totals['reports']} reports ({totals['read']} read, "
              f"{totals['to_read']} to read, {totals['paper_not_read']} paper not read, "
              f"{totals['failed']} failed), {totals['transactions']} transactions")
    if history_complete:
        print(f"History: complete back to {HISTORY_START:%m/%Y}")
    elif history_day:
        print(f"History: listed back to {history_day:%m/%Y} (it goes back to "
              f"{HISTORY_START:%m/%Y})")
    if web is not None:
        print(f"Requests to eFD in this run: {web.requests_sent}")
    if status in ADVICE:
        print("\n".join(ADVICE[status]))
    if conn is not None and run_id is not None:
        try:
            database.finish_run(conn, run_id, status, verdict_line, {
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
