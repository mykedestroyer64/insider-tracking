"""
collector.py - the Senate collector: saves senators' periodic transaction reports from eFD.

GitHub Actions runs it on a schedule (see .github/workflows/collect.yml). Each run:
  1. connects to the database (and creates the tables the first time)
  2. opens eFD and accepts its usage agreement
  3. looks for reports received in the last 60 days and stores the new ones
  4. lists older reports, two years per run, back to January 2012
  5. reads electronic reports that haven't been read yet, newest first, and saves their
     transactions
Scanned paper reports are stored with status "not_read" and their link.

It sends at most 200 requests to eFD per run, 2-3 seconds apart, so the full history fills in
over several days. Everything is saved as it goes: a run that stops early loses nothing, and
the next run carries on.
"""

from datetime import date, timedelta

from ...core.errors import OutOfRequests, SiteStop
from ...core.identity import user_agent
from ...core.status import BLOCKED, CHANGED, DB_ERROR, ERROR, SITE_DOWN
from ...core.text import plural
from ...db.congress import CongressReports
from ...db.connection import DatabaseError
from ...db.state import StateStore
from ...runs.command import CollectorCommand
from ...runs.definition import CollectorDefinition
from ...runs.steps import StepResult
from ...web.client import PoliteClient, new_session
from ...web.pacing import RequestBudget
from ..congress import plain_kinds
from .parsing import ReportProblem, parse_report_page, parse_search_row
from .site import PACING, EfdEndpoints, EfdSite

VERSION = "0.2"
RECENT_DAYS = 60            # every run re-checks reports received in the last 60 days
HISTORY_START = date(2012, 1, 1)
MONTHS_PER_RUN = 24         # months of history to list per run
FAILURES_BEFORE_STOP = 3    # unreadable reports in a row before we assume the site changed

DEFINITION = CollectorDefinition(
    key="senate",
    title="collector",
    version=VERSION,
    description="Collect US Senate periodic transaction reports from eFD into the database.",
    steps=("Connect to the database", "Open eFD and accept the agreement", "Check for new reports",
           "List older reports (history)", "Read reports"),
    max_requests=200,
    site="eFD",
    pace_note=", 2-3 seconds apart",
    advice={
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
    },
)


def iso_date(text):
    return date.fromisoformat(text) if text else None


def month_start_before(day):
    """The first day of the month that comes before `day` (or of its own month)."""
    start = day.replace(day=1)
    if start == day:
        start = (day - timedelta(days=1)).replace(day=1)
    return max(start, HISTORY_START)


def report_count(count):
    return plural(count, "report", separators=False)


class SenateSource:
    """The Senate collector, with the eFD addresses and pacing to use (tests pass a stand-in site)."""

    definition = DEFINITION

    def __init__(self, endpoints=EfdEndpoints(), pacing=PACING, read_page=parse_report_page):
        self.endpoints = endpoints
        self.pacing = pacing
        self.read_page = read_page

    def start(self, context):
        return SenateRun(context, self)


class SenateRun:
    def __init__(self, context, source):
        self.context = context
        self.source = source
        self.reports = CongressReports(context.db)
        self.state = StateStore(context.db)
        self.site = None
        self.history = None
        self.stats = {"new_reports": 0, "reports_read": 0, "reports_failed": 0, "transactions_saved": 0}

    # ------------------------------------------------------------- the run

    def prepare(self):
        return f"tables ready, {self.reports.counts()['reports']} reports stored so far"

    def open(self):
        session = new_session(user_agent(VERSION, self.context.settings.email))
        client = PoliteClient(session, self.source.pacing, RequestBudget(self.context.max_requests),
                              sleep=self.context.sleep)
        self.site = EfdSite(client, self.source.endpoints)

    def steps(self):
        return [self.accept_agreement, self.check_recent, self.list_history, self.read_reports]

    @property
    def requests_sent(self):
        return self.site.client.requests_sent if self.site else None

    def budget_used_up(self):
        return "this run's request budget is used up"

    # ----------------------------------------------------------- the steps

    def accept_agreement(self):
        """Step 2."""
        return StepResult("agreement accepted", self.site.open())

    def check_recent(self):
        """Step 3: reports received in the last RECENT_DAYS days (more after a long pause)."""
        today = self.context.today
        last_scan = iso_date(self.state.get("recent_scan_day"))
        recent_from = min(last_scan or today, today) - timedelta(days=RECENT_DAYS)
        reports = self.read_rows(self.site.search_all(recent_from, today + timedelta(days=1)))
        new = self.reports.add(reports)
        self.state.set("recent_scan_day", today.isoformat())
        if self.state.get("history_listed_back_to") is None:
            self.state.set("history_listed_back_to", recent_from.isoformat())
        self.stats["new_reports"] += len(new)
        detail = f"{report_count(len(reports))} received since {recent_from:%m/%d/%Y}, {len(new)} new"
        if new:
            detail += f" ({plain_kinds(new)})"
        return detail

    def list_history(self):
        """Step 4: list older reports month by month, going back to HISTORY_START."""
        cursor = iso_date(self.state.get("history_listed_back_to"))
        if cursor is None:
            return "nothing to do yet - the first check for new reports hasn't finished"
        if cursor <= HISTORY_START:
            return f"complete - every report back to {HISTORY_START:%m/%Y} is listed"
        new_reports, months = [], 0
        try:
            while cursor > HISTORY_START and months < MONTHS_PER_RUN:
                start = month_start_before(cursor)
                reports = self.read_rows(self.site.search_all(start, cursor))
                new_reports += self.reports.add(reports)
                cursor = start
                self.state.set("history_listed_back_to", cursor.isoformat())
                months += 1
        except OutOfRequests:
            self.context.console.note("stopped listing: this run's request budget is used up")
        self.stats["new_reports"] += len(new_reports)
        if cursor <= HISTORY_START:
            return f"complete - listed back to {HISTORY_START:%m/%Y} ({len(new_reports)} new this run)"
        return (f"listed {plural(months, 'more month', separators=False)}, back to {cursor:%m/%Y}: "
                f"{plural(len(new_reports), 'new report', separators=False)} ({plain_kinds(new_reports)})")

    def read_reports(self):
        """Step 5: download and read electronic reports, newest first, within the budget."""
        stats, note = self.stats, self.context.console.note
        if self.site.client.requests_left == 0:
            left = self.reports.counts()["to_read"]
            return f"no requests left in this run's budget; {left} left to read"
        queue = self.reports.to_read(limit=self.site.client.requests_left)
        if not queue:
            return "nothing to read - every electronic report found so far is read"
        note(f"reading up to {len(queue)} reports, newest first")
        in_a_row = 0
        for number, report in enumerate(queue, 1):
            try:
                response = self.site.get_report(report["url"])
            except OutOfRequests:
                break
            path = report["report_path"]
            problem = None
            if response.status_code in (404, 410):
                problem = f"page not found (HTTP {response.status_code})"
                self.reports.mark_failed(path, problem)
            else:
                try:
                    _, transactions, odd_rows = self.source.read_page(response.text)
                except ReportProblem as error:
                    problem = str(error)
                    self.reports.mark_failed(path, problem, response.content)
                else:
                    remark = None
                    if odd_rows:
                        remark = f"{odd_rows} table rows had a different number of cells and were skipped"
                    self.reports.save_read(path, transactions, response.content, remark)
                    stats["reports_read"] += 1
                    stats["transactions_saved"] += len(transactions)
            if problem:
                stats["reports_failed"] += 1
                in_a_row += 1
                note(f"could not read {report['first_name']} {report['last_name']} "
                     f"({report['date_received']}): {problem}")
                if in_a_row >= FAILURES_BEFORE_STOP:
                    raise SiteStop(CHANGED, f"{in_a_row} reports in a row could not be read "
                                            f"(last: {problem})", response)
            else:
                in_a_row = 0
            if number % 25 == 0:
                note(f"{number} of {len(queue)} done")

        left = self.reports.counts()["to_read"]
        detail = (f"read {report_count(stats['reports_read'])}, "
                  f"{plural(stats['transactions_saved'], 'transaction', separators=False)} saved")
        if stats["reports_failed"]:
            detail += f", {stats['reports_failed']} could not be read"
        return detail + f"; {left} left to read"

    def read_rows(self, rows):
        """Turn search result rows into report dicts (each report once)."""
        reports = {}
        for row in rows:
            report = parse_search_row(row, self.site.base_url)
            if report is None:
                raise SiteStop(CHANGED, "a search result row does not have the expected "
                                        "columns (first name, last name, filer, report "
                                        "link, date)")
            reports.setdefault(report["report_path"], report)
        return list(reports.values())

    # ------------------------------------------------------- the summing up

    @property
    def history_complete(self):
        return self.history is not None and self.history <= HISTORY_START

    def read_totals(self):
        totals = self.reports.counts()
        try:
            self.history = iso_date(self.state.get("history_listed_back_to"))
        except DatabaseError:
            pass
        return totals

    def outcome(self, totals):
        stats = self.stats
        if (stats["new_reports"] == 0 and stats["reports_read"] == 0 and totals
                and totals["to_read"] == 0 and self.history_complete):
            return "- no new reports, everything is up to date"
        outcome = (f"- {plural(stats['new_reports'], 'new report', separators=False)}, "
                   f"{stats['reports_read']} read "
                   f"({plural(stats['transactions_saved'], 'transaction', separators=False)} saved)")
        if totals:
            outcome += f", {totals['to_read']} left to read"
        return outcome

    def closing_lines(self, totals):
        lines = []
        if totals:
            lines.append(f"Database now: {totals['reports']} reports ({totals['read']} read, "
                         f"{totals['to_read']} to read, {totals['paper_not_read']} paper not read, "
                         f"{totals['failed']} failed), {totals['transactions']} transactions")
        if self.history_complete:
            lines.append(f"History: complete back to {HISTORY_START:%m/%Y}")
        elif self.history:
            lines.append(f"History: listed back to {self.history:%m/%Y} (it goes back to {HISTORY_START:%m/%Y})")
        return lines


def command():
    """`python -m insider_tracking collect senate`."""
    return CollectorCommand(SenateSource())
