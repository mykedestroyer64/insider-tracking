"""
collect_returns.py - works out how each traded stock did afterwards, with Sharadar's prices.

GitHub Actions runs this every 6 hours (see .github/workflows/collect-returns.yml).
Each run:
  1. connects to the database (and adds the trade_returns table the first time)
  2. gets SPY's prices (the S&P 500 fund) and lists the trades that need returns:
     stock trades in Congress reports (found by ticker) and company insiders' trades
     (found by the company's SEC number) whose 1-month, 6-month or 1-year period has
     ended since they were last worked out
  3. gets Sharadar's list of securities and finds the security each trade was in
  4. gets each security's prices and works out the returns, saving them as it goes
Prices are never stored or shown, only the returns (Sharadar's licence); the key is
sent in a request header, never in an address.

It needs a Sharadar subscription: the API key goes in the SHARADAR_API_KEY secret.
Without it the run does nothing and says so; that doesn't count as a failure.

Settings: SHARADAR_API_KEY, SUPABASE_DB_URL, SUPABASE_DB_PASSWORD (GitHub secrets).
The last line printed is the verdict. Exit code: 0 if OK (or no key yet), 1 otherwise.
"""

import argparse
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import database
import returns_calc
import sharadar_site
from returns_calc import PERIODS, Series, stock_key
from sharadar_site import BLOCKED, CHANGED, KEY_PROBLEM, OK, SITE_DOWN

PROJECT = "insider-tracking"
VERSION = "0.1"

MAX_REQUESTS = 2500        # requests to Sharadar per run
RETRY_FAILED_DAYS = 30     # trades whose security wasn't found are tried again after this many days
FIRST_DAY = date(2012, 1, 1)
SAVE_EVERY = 100           # securities between saves
BENCHMARK_FROM = FIRST_DAY - timedelta(days=15)

DB_ERROR, ERROR, NOT_RUN = "DB_ERROR", "ERROR", "NOT_RUN"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

STEP_NAMES = {
    1: "Connect to the database",
    2: "Find trades that need returns",
    3: "Find each trade's security",
    4: "Work out returns",
}
TOTAL_STEPS = len(STEP_NAMES)

ADVICE = {
    KEY_PROBLEM: ["Sharadar didn't accept the API key. Check the SHARADAR_API_KEY secret (Settings >",
                  "Secrets and variables > Actions) against the key on sharadar.com/account, and that",
                  "the subscription is active."],
    BLOCKED: ["Sharadar refused the requests. Don't change anything; share this result."],
    SITE_DOWN: ["Sharadar had a server error. The next run tries again."],
    CHANGED: ["Sharadar's answers don't look the way the collector expects. Share this log."],
    DB_ERROR: ["Check the SUPABASE_DB_URL and SUPABASE_DB_PASSWORD secrets, and that the Supabase",
               "project isn't paused."],
    ERROR: ["The collector hit a problem it didn't expect. Share this log."],
}


class StepLog:
    def __init__(self):
        self.steps = []

    def done(self, number, status, detail=""):
        label = f"{STEP_NAMES[number]} ".ljust(36, ".")
        print(f"[{number}/{TOTAL_STEPS}] {label} {status}" + (f" - {detail}" if detail else ""), flush=True)
        self.steps.append({"step": number, "name": STEP_NAMES[number], "status": status, "detail": detail})

    def rest(self, after, status):
        for number in range(after + 1, TOTAL_STEPS + 1):
            self.done(number, status)


def plural(count, word, many=None):
    return f"{count:,} " + (word if count == 1 else (many or word + "s"))


def securities(count):
    return plural(count, "security", "securities")


# --------------------------------------------------------------------- steps

def pending_items(items, stored, market_last, today):
    """The trades whose returns are missing, or whose period has ended since they were worked out."""
    pending, seen = [], set()
    for source, ident, day, hint in items:
        if day is None or day < FIRST_DAY or day > market_last:
            continue
        key = stock_key(source, ident)
        if (key, day) in seen:
            continue
        seen.add((key, day))
        row = stored.get((key, day))
        if row is None:
            pending.append((key, source, ident, day, hint))
            continue
        values, note, checked = row[:3], row[3], row[4]
        if note and not note.startswith("stopped"):
            if (today - checked).days >= RETRY_FAILED_DAYS:
                pending.append((key, source, ident, day, hint))
            continue
        for period, value in zip(PERIODS, values):
            end = day + timedelta(days=period)
            if value is None and end <= market_last and checked < end:
                pending.append((key, source, ident, day, hint))
                break
    return pending


def result_row(key, day, security, result, checked):
    row = {"stock_key": key, "trade_date": day, "security": security, "note": result.note, "checked_on": checked}
    for period in PERIODS:
        stock, market = result.stock.get(period), result.market.get(period)
        row[f"r{period}"] = None if stock is None else round(stock, 6)
        row[f"m{period}"] = None if market is None else round(market, 6)
    return row


def failure_row(key, day, note, checked):
    return {"stock_key": key, "trade_date": day, "security": None, "note": note, "checked_on": checked,
            **{f"r{p}": None for p in PERIODS}, **{f"m{p}": None for p in PERIODS}}


def match(universe, pending, checked):
    """Group the pending trades by the security they were in; trades with no security get a failure row."""
    groups, failures = {}, []
    for key, source, ident, day, hint in pending:
        if source == "congress":
            security, why = universe.for_ticker(ident, day)
        else:
            security, why = universe.for_company(ident, day, hint)
        if security is None:
            failures.append(failure_row(key, day, why, checked))
            continue
        group = groups.setdefault((security.table, security.ticker), {"security": security, "items": []})
        group["items"].append((key, day))
    return groups, failures


def work_out(conn, web, groups, market, market_last, stats):
    """Step 4: one request per security, then the returns of every pending trade in it."""
    ordered = sorted(groups.values(), key=lambda g: (-len(g["items"]), g["security"].ticker))
    rows, done = [], 0
    try:
        for group in ordered:
            security, items = group["security"], group["items"]
            days = [day for _, day in items]
            start = min(days) - timedelta(days=returns_calc.PRICE_GAP_DAYS + 3)
            end = min(max(days) + timedelta(days=max(PERIODS) + returns_calc.PRICE_GAP_DAYS), market_last)
            series = Series.from_csv(web.closes(security.table, security.ticker, start, end))
            stopped = returns_calc.stopped_day(series, security, market_last) if len(series) else None
            for key, day in items:
                if not len(series):
                    rows.append(failure_row(key, day, "no prices from Sharadar", market_last))
                    stats["no_prices"] += 1
                    continue
                result = returns_calc.returns(series, market, day, market_last, stopped)
                rows.append(result_row(key, day, security.ticker, result, market_last))
                stats["worked_out"] += 1
            done += 1
            stats["securities"] += 1
            if len(rows) >= SAVE_EVERY * 10 or done % SAVE_EVERY == 0:
                database.save_returns(conn, rows)
                rows = []
    except sharadar_site.OutOfRequests:
        stats["left"] = len(ordered) - done
        raise
    finally:
        if rows:
            database.save_returns(conn, rows)
    return f"{securities(done)}, {plural(stats['worked_out'], 'trade')} worked out"


# ---------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Work out how traded stocks did afterwards (Sharadar prices).")
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS,
                        help=f"requests to Sharadar in this run (default {MAX_REQUESTS})")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    ran_on = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "your computer"
    started = datetime.now(timezone.utc)
    today = started.date()
    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"{PROJECT} returns collector {VERSION}")
    print(f"Running on: {ran_on}")
    print(f"At most {args.max_requests} requests to Sharadar in this run\n")

    api_key = (os.environ.get("SHARADAR_API_KEY") or "").strip()
    db_url = (os.environ.get("SUPABASE_DB_URL") or "").strip()
    db_password = os.environ.get("SUPABASE_DB_PASSWORD") or ""
    if not api_key:
        print("There is no SHARADAR_API_KEY secret yet, so nothing was done. Add it under Settings >")
        print("Secrets and variables > Actions once the Sharadar subscription has started.")
        print(f"VERDICT: {NOT_RUN} - no Sharadar key yet (ran on {ran_on})")
        return 0
    if not db_url:
        print(f"VERDICT: {NOT_RUN} - SUPABASE_DB_URL is missing (ran on {ran_on})")
        return 1

    log = StepLog()
    stats = {"items": 0, "pending": 0, "unmatched": 0, "securities": 0, "worked_out": 0, "no_prices": 0, "left": 0}
    status, failed_step, failure = OK, None, None
    conn = run_id = web = None
    step = 1
    try:
        conn = database.connect(db_url, db_password)
        database.ensure_schema(conn)
        before = database.returns_counts(conn)
        run_id = database.start_run(conn, ran_on, collector="returns")
        log.done(1, OK, f"tables ready, {before['rows']:,} trade returns stored so far")

        web = sharadar_site.SharadarSession(api_key, f"{PROJECT}/{VERSION} (non-commercial public transparency "
                                                     "project)", args.max_requests)
        step = 2
        market = Series.from_csv(web.closes("funds", returns_calc.BENCHMARK, BENCHMARK_FROM, today))
        if len(market) < 100:
            raise sharadar_site.SiteStop(CHANGED, f"only {len(market)} prices for {returns_calc.BENCHMARK}")
        market_last = market.last_day
        items = database.return_items(conn)
        stats["items"] = len(items)
        pending = pending_items(items, database.stored_returns(conn), market_last, today)
        stats["pending"] = len(pending)
        log.done(2, OK, f"{len(items):,} trade days with stocks; {len(pending):,} need returns "
                        f"(market prices up to {market_last})")

        if pending:
            step = 3
            universe = returns_calc.Universe.from_csv(web.table_file("tickers"))
            groups, failures = match(universe, pending, market_last)
            stats["unmatched"] = len(failures)
            database.save_returns(conn, failures)
            reasons = {}
            for row in failures:
                reasons[row["note"]] = reasons.get(row["note"], 0) + 1
            log.done(3, OK, f"{len(universe.securities):,} securities listed; {securities(len(groups))} "
                            f"to read; {len(failures):,} trades without one"
                            + (" (" + ", ".join(f"{n:,} {why}" for why, n in sorted(reasons.items())) + ")"
                               if reasons else ""))
            step = 4
            log.done(4, OK, work_out(conn, web, groups, market, market_last, stats))
        else:
            log.done(3, OK, "nothing to find")
            log.done(4, OK, "nothing to work out")

    except sharadar_site.SiteStop as stop:
        status, failed_step, failure = stop.status, step, stop.detail
        log.done(step, status, stop.detail)
        log.rest(step, NOT_RUN)
    except sharadar_site.OutOfRequests:
        log.done(step, OK, f"this run's request budget is used up; {stats['left']:,} securities left for the next run")
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

    totals, whole_db = None, ""
    if conn is not None and status != DB_ERROR:
        try:
            totals = database.returns_counts(conn)
            whole_db = f" (whole database {database.total_size(conn)} MB of the free plan's {database.FREE_PLAN_MB} MB)"
        except database.DatabaseError:
            pass
    if failure is not None:
        outcome = f"at step {failed_step} ({STEP_NAMES[failed_step]}) - {failure}"
    elif not stats["pending"]:
        outcome = "- nothing new, every return is up to date"
    else:
        outcome = (f"- returns worked out for {plural(stats['worked_out'], 'trade day')} in {securities(stats['securities'])}"
                   + (f"; {stats['left']:,} securities left for the next run" if stats["left"] else ""))
    verdict_line = f"VERDICT: {status} {outcome} (ran on {ran_on})"

    print()
    if totals:
        print(f"Database now: {totals['rows']:,} trade days with returns ({totals['r30']:,} with 1 month, "
              f"{totals['r182']:,} with 6 months, {totals['r365']:,} with 1 year; {totals['stopped']:,} stopped "
              f"trading early; {totals['failed']:,} without a security or prices); table uses {totals['size']}"
              + whole_db)
    if web is not None:
        print(f"Requests to Sharadar in this run: {web.requests_sent}")
    if status in ADVICE:
        print("\n".join(ADVICE[status]))
    if conn is not None and run_id is not None:
        try:
            database.finish_run(conn, run_id, status, verdict_line, {
                "collector": "returns", "started_at_utc": started.isoformat(timespec="seconds"),
                "steps": log.steps, "stats": stats, "totals": totals,
                "requests": web.requests_sent if web else 0})
        except database.DatabaseError:
            pass
        try:
            conn.close()
        except Exception:
            pass
    print(verdict_line)
    return 0 if status == OK else 1


if __name__ == "__main__":
    sys.exit(main())
