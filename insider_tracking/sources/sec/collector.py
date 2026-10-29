"""
collector.py - the company-insider collector: officers' and directors' buys and sells (SEC Form 4).

GitHub Actions runs it every 3 hours (see .github/workflows/collect-sec.yml). Each run:
  1. connects to the database (and adds the insider tables the first time)
  2. loads the SEC's quarterly data files, newest first, two per run, back to 2021 (each
     file holds every Form 4 of a quarter; only officers' and directors' buys and sells are
     kept)
  3. reads EDGAR's daily lists of filings for the days after the newest quarterly file,
     downloads each new Form 4 and keeps the same kind of trades
Filings seen before are updated, never duplicated, so the two sources can overlap.

It sends at most 1,200 requests to the SEC per run, 0.3-0.6 seconds apart, with a User-Agent
that names the project and gives a contact email, as the SEC asks. Everything is saved as it
goes.
"""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from ...core.errors import OutOfRequests, SiteStop
from ...core.identity import user_agent
from ...core.status import BLOCKED, CHANGED, DB_ERROR, ERROR, SITE_DOWN
from ...core.text import plural
from ...db.insiders import InsiderFilings
from ...db.size import DatabaseSize
from ...db.state import StateStore
from ...runs.command import CollectorCommand
from ...runs.definition import CollectorDefinition
from ...runs.summary import whole_database_note
from ...web.checks import unexpected
from ...web.client import PoliteClient, new_session
from ...web.pacing import RequestBudget
from .names import ENTITY_SQL
from .parsing import FilingProblem, parse_daily_index, parse_filing, parse_quarter
from .site import PACING, WHO, SecEndpoints, SecSite

VERSION = "0.5"
FIRST_QUARTER = (2021, 1)  # how far back the quarterly files are loaded
QUARTERS_PER_RUN = 2
DAY_COMPLETE_AFTER = timedelta(hours=6)   # a day's list is final 6 hours after that day ends (UTC), i.e. after 1 am in Washington
FAILURES_BEFORE_STOP = 5   # unreadable filings in a row before we assume EDGAR changed

DEFINITION = CollectorDefinition(
    key="sec",
    title="company-insider collector",
    version=VERSION,
    description="Collect company insiders' buys and sells (SEC Form 4) into the database.",
    steps=("Connect to the database", "Load quarterly files (history)", "Read new filings (daily lists)"),
    max_requests=1200,
    site=WHO,
    advice={
        BLOCKED: ["The SEC refused the collector. It asks for a User-Agent with a name and an email and",
                  "fewer than 10 requests a second, which this collector does. The next scheduled run",
                  "tries again in 3 hours; if several runs in a row are BLOCKED, stop and share this result."],
        SITE_DOWN: ["The SEC's site had a server error. The next scheduled run tries again in 3 hours."],
        CHANGED: ["A page or file did not look the way the collector expects, so the SEC's site may",
                  "work differently now. Share this log so the collector can be updated."],
        DB_ERROR: ["Check the SUPABASE_DB_URL secret (it must be Supabase's Session pooler string),",
                   "the SUPABASE_DB_PASSWORD secret, and that the Supabase project isn't paused."],
        ERROR: ["The collector hit a problem it didn't expect. Share this log."],
    },
)


def quarter_of(day):
    return day.year, (day.month - 1) // 3 + 1


def quarter_end(year, quarter):
    month = quarter * 3
    first_of_next = date(year + (month == 12), (month % 12) + 1, 1)
    return first_of_next - timedelta(days=1)


def quarter_name(year, quarter):
    return f"{year}q{quarter}"


class SecSource:
    definition = DEFINITION

    def __init__(self, endpoints=SecEndpoints(), pacing=PACING, first_quarter=FIRST_QUARTER):
        self.endpoints = endpoints
        self.pacing = pacing
        self.first_quarter = first_quarter

    def start(self, context):
        return SecRun(context, self)


class SecRun:
    def __init__(self, context, source):
        self.context = context
        self.source = source
        self.filings = InsiderFilings(context.db)
        self.state = StateStore(context.db)
        self.size = DatabaseSize(context.db)
        self.site = None
        self.files = None
        self.stats = {"filings_saved": 0, "trades_saved": 0, "filings_fetched": 0, "suspect": 0}

    # ------------------------------------------------------------- the run

    def prepare(self):
        removed = self.filings.remove_entities(ENTITY_SQL)
        before = self.filings.counts()
        return (f"tables ready, {before['trades']:,} insider trades stored so far"
                + (f" ({removed:,} rows by funds or companies removed)" if removed else ""))

    def open(self):
        session = new_session(user_agent(VERSION, self.context.settings.email), compressed=True)
        client = PoliteClient(session, self.source.pacing, RequestBudget(self.context.max_requests), who=WHO,
                              sleep=self.context.sleep)
        self.site = SecSite(client, self.source.endpoints)

    def steps(self):
        return [self.load_quarters, self.read_daily]

    @property
    def requests_sent(self):
        return self.site.client.requests_sent if self.site else None

    def budget_used_up(self):
        return "this run's request budget is used up"

    # ----------------------------------------------------------- the steps

    def load_quarters(self):
        """Step 2: load the quarterly files not loaded yet, newest first, QUARTERS_PER_RUN per run."""
        self.files = files = self.site.quarterly_files()
        stats = self.stats
        done = set(filter(None, (self.state.get("sec_quarters_done") or "").split(",")))
        wanted = sorted((q for q in files if q >= self.source.first_quarter and quarter_name(*q) not in done),
                        reverse=True)
        if not wanted:
            return (f"complete - every quarterly file from {quarter_name(*self.source.first_quarter)} "
                    f"to {quarter_name(*max(files))} is loaded")
        loaded = []
        for year, quarter in wanted[:QUARTERS_PER_RUN]:
            name = quarter_name(year, quarter)
            with tempfile.TemporaryDirectory() as folder:
                response = self.site.quarterly_file(files[(year, quarter)])
                if not response.content.startswith(b"PK"):
                    raise unexpected(response, f"the {name} file is not a ZIP file")
                path = Path(folder) / f"{name}.zip"
                path.write_bytes(response.content)
                try:
                    filings, trades, counts = parse_quarter(path)
                except (FilingProblem, OSError) as problem:
                    raise SiteStop(CHANGED, f"the {name} file could not be read: {problem}") from None
            new = self.filings.save(filings, trades, source=name)
            done.add(name)
            self.state.set("sec_quarters_done", ",".join(sorted(done)))
            stats["filings_saved"] += len(filings)
            stats["trades_saved"] += len(trades)
            stats["suspect"] += counts["suspect"]
            loaded.append(f"{name}: {len(response.content) / 1e6:.0f} MB, {counts['form4']:,} Form 4s, kept "
                          f"{plural(len(trades), 'trade')} by officers and directors in {len(filings):,} filings "
                          f"({new:,} new)")
        for line in loaded:
            self.context.console.note(line)
        left = len(wanted) - len(loaded)
        return f"loaded {len(loaded)}; {left} quarterly file{'s' if left != 1 else ''} left to load"

    def read_daily(self):
        """Step 3: new filings from EDGAR's daily lists, for the days after the newest quarterly file.

        EDGAR publishes no list on weekends and US federal holidays, and asking for a list that
        doesn't exist gets "403 Access Denied" from the SEC's storage, which looks just like a
        refusal. So a day's list is asked for only when EDGAR's listing of the quarter has it."""
        now, stats, note = self.context.started, self.stats, self.context.console.note
        newest = max(self.files)
        first_day = quarter_end(*newest) + timedelta(days=1)
        cursor = self.state.get("sec_daily_cursor")        # the last day fully processed
        day = max(first_day, date.fromisoformat(cursor) + timedelta(days=1)) if cursor else first_day
        today = (now - DAY_COMPLETE_AFTER).date() - timedelta(days=1)   # the newest day whose list is final
        if day > now.date():
            return f"up to date - the daily lists after {quarter_name(*newest)} are read through {cursor}"
        seen_state = json.loads(self.state.get("sec_daily_seen") or "{}")
        days_done, fetched, kept, failures_in_a_row = 0, 0, 0, 0
        progress, stopped = [], None          # what happened on each day, for the step's summary line
        listings = {}
        try:
            while day <= now.date():
                if day.weekday() >= 5:                                # EDGAR publishes no lists at weekends
                    if day <= today:
                        self.state.set("sec_daily_cursor", day.isoformat())
                    day += timedelta(days=1)
                    continue
                year, quarter = quarter_of(day)
                if (year, quarter) not in listings:
                    listings[(year, quarter)] = self.site.listed_days(year, quarter)
                if f"{day:%Y%m%d}" not in listings[(year, quarter)]:
                    if day <= today:                                  # a weekday without a list: a holiday
                        progress.append(f"{day:%m/%d}: no list (EDGAR closed)")
                        self.state.set("sec_daily_cursor", day.isoformat())
                        day += timedelta(days=1)
                        continue
                    break                                             # not published yet
                response = self.site.daily_index(year, quarter, day)
                if response.status_code == 404:                       # a holiday, or not published yet
                    if day <= today:
                        self.state.set("sec_daily_cursor", day.isoformat())
                        day += timedelta(days=1)
                        continue
                    break
                listed = parse_daily_index(response.text)
                if not listed and "Form Type" not in response.text:
                    raise unexpected(response, f"the daily list for {day} could not be read")
                seen = set(seen_state.get("accessions", [])) if seen_state.get("day") == day.isoformat() else set()
                todo = [f for f in listed if f["accession"] not in seen]
                day_fetched, day_kept = 0, 0
                for filing in todo:
                    try:
                        page = self.site.filing(filing["path"])
                    except OutOfRequests:
                        stopped = f"{day:%m/%d}: {len(seen)} of {len(listed)} read, budget used up"
                        raise
                    fetched += 1
                    day_fetched += 1
                    if page.status_code == 404:
                        note(f"filing {filing['accession']} is listed but not available (HTTP 404)")
                    else:
                        try:
                            parsed, trades = parse_filing(page.text, filing["accession"], filing["filed"])
                        except FilingProblem as problem:
                            failures_in_a_row += 1
                            note(f"could not read filing {filing['accession']}: {problem}")
                            if failures_in_a_row >= FAILURES_BEFORE_STOP:
                                raise SiteStop(CHANGED, f"{failures_in_a_row} filings in a row could not be "
                                                        f"read (last: {problem})", page)
                        else:
                            failures_in_a_row = 0
                            if trades:
                                self.filings.save([parsed], trades, source=f"daily {day}")
                                kept += 1
                                day_kept += 1
                                stats["filings_saved"] += 1
                                stats["trades_saved"] += len(trades)
                                stats["suspect"] += sum(1 for t in trades if t["suspect"])
                    seen.add(filing["accession"])
                    seen_state = {"day": day.isoformat(), "accessions": sorted(seen)}
                    if len(seen) % 25 == 0:
                        self.state.set("sec_daily_seen", json.dumps(seen_state))
                self.state.set("sec_daily_seen", json.dumps(seen_state))
                progress.append(f"{day:%m/%d}: {len(listed):,} listed, {day_fetched:,} read, {day_kept} kept")
                if day <= today:
                    self.state.set("sec_daily_cursor", day.isoformat())
                    self.state.set("sec_daily_seen", "{}")
                    days_done += 1
                    day += timedelta(days=1)
                else:
                    break                                             # today's list isn't final: re-read it next run
        except OutOfRequests:
            note("stopped: this run's request budget is used up")
            self.state.set("sec_daily_seen", json.dumps(seen_state))
        stats["filings_fetched"] += fetched
        cursor = self.state.get("sec_daily_cursor") or "none yet"
        detail = (f"{plural(days_done, 'day')} finished (through {cursor}); {fetched:,} Form 4s read, "
                  f"{kept:,} with officers' or directors' buys or sells")
        if progress or stopped:
            detail += " [" + "; ".join(progress + ([stopped] if stopped else [])) + "]"
        return detail

    # ------------------------------------------------------- the summing up

    def read_totals(self):
        return self.filings.counts()

    def outcome(self, totals):
        stats = self.stats
        if stats["filings_saved"] == 0 and stats["filings_fetched"] == 0:
            return "- nothing new, everything is up to date"
        outcome = (f"- {plural(stats['trades_saved'], 'trade')} saved from "
                   f"{plural(stats['filings_saved'], 'filing')}")
        if stats["filings_fetched"]:
            outcome += f", {stats['filings_fetched']:,} new filings read"
        return outcome

    def closing_lines(self, totals):
        if not totals:
            return []
        return [f"Database now: {totals['trades']:,} insider trades ({totals['buys']:,} buys, {totals['sells']:,} sells, "
                f"{totals['suspect']:,} suspect) from {totals['filings']:,} filings by {totals['insiders']:,} insiders, "
                f"filed {totals['first_filing']} to {totals['last_filing']}; tables use {totals['size']}"
                + whole_database_note(self.size)]


def command():
    """`python -m insider_tracking collect sec`."""
    return CollectorCommand(SecSource())
