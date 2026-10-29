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
from ..report_queue import ReadResult, ReportQueue
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


def count(number, word):
    return plural(number, word, separators=False)


class SenateSource:
    """The Senate collector, with the eFD addresses and pacing to use (tests pass a stand-in
    site), and the report reader (tests pass one that fails)."""

    definition = DEFINITION

    def __init__(self, endpoints=EfdEndpoints(), pacing=PACING, read_page=parse_report_page):
        self.endpoints = endpoints
        self.pacing = pacing
        self.read_page = read_page

    def start(self, context):
        budget = RequestBudget(context.max_requests)

        def open_site():
            session = new_session(user_agent(VERSION, context.settings.email))
            return EfdSite(PoliteClient(session, self.pacing, budget, sleep=context.sleep), self.endpoints)

        reports = CongressReports(context.db)
        return SenateRun(reports=reports, bookmarks=StateStore(context.db), open_site=open_site, budget=budget,
                         reader=PageReader(reports, self.read_page), today=context.today, note=context.console.note)


class PageReader:
    """Reads one downloaded Senate report page and saves its transactions (and the page)."""

    def __init__(self, reports, read_page):
        self.reports = reports
        self.read_page = read_page

    def read(self, report, response):
        path = report["report_path"]
        if response.status_code in (404, 410):
            problem = f"page not found (HTTP {response.status_code})"
            self.reports.mark_failed(path, problem)
            return ReadResult(problem=problem)
        try:
            _, transactions, odd_rows = self.read_page(response.text)
        except ReportProblem as error:
            self.reports.mark_failed(path, str(error), response.content)
            return ReadResult(problem=str(error))
        remark = None
        if odd_rows:
            remark = f"{odd_rows} table rows had a different number of cells and were skipped"
        self.reports.save_read(path, transactions, response.content, remark)
        return ReadResult(transactions=len(transactions))


class SenateRun:
    """reports: a ReportStore; bookmarks: a Bookmarks store; open_site() -> an EfdSite."""

    def __init__(self, reports, bookmarks, open_site, budget, reader, today, note):
        self.reports = reports
        self.bookmarks = bookmarks
        self.open_site = open_site
        self.budget = budget
        self.reader = reader
        self.today = today
        self.note = note
        self.site = None
        self.history = None
        self.stats = {"new_reports": 0, "reports_read": 0, "reports_failed": 0, "transactions_saved": 0}

    # ------------------------------------------------------------- the run

    def prepare(self):
        return f"tables ready, {self.reports.counts()['reports']} reports stored so far"

    def open(self):
        self.site = self.open_site()

    def steps(self):
        return [self.accept_agreement, self.check_recent, self.list_history, self.read_reports]

    @property
    def requests_sent(self):
        return self.budget.sent if self.site else None

    def budget_used_up(self):
        return "this run's request budget is used up"

    # ----------------------------------------------------------- the steps

    def accept_agreement(self):
        """Step 2."""
        return StepResult("agreement accepted", self.site.open())

    def check_recent(self):
        """Step 3: reports received in the last RECENT_DAYS days (more after a long pause)."""
        today = self.today
        last_scan = iso_date(self.bookmarks.get("recent_scan_day"))
        recent_from = min(last_scan or today, today) - timedelta(days=RECENT_DAYS)
        reports = self.read_rows(self.site.search_all(recent_from, today + timedelta(days=1)))
        new = self.reports.add(reports)
        self.bookmarks.set("recent_scan_day", today.isoformat())
        if self.bookmarks.get("history_listed_back_to") is None:
            self.bookmarks.set("history_listed_back_to", recent_from.isoformat())
        self.stats["new_reports"] += len(new)
        detail = f"{count(len(reports), 'report')} received since {recent_from:%m/%d/%Y}, {len(new)} new"
        if new:
            detail += f" ({plain_kinds(new)})"
        return detail

    def list_history(self):
        """Step 4: list older reports month by month, going back to HISTORY_START."""
        cursor = iso_date(self.bookmarks.get("history_listed_back_to"))
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
                self.bookmarks.set("history_listed_back_to", cursor.isoformat())
                months += 1
        except OutOfRequests:
            self.note("stopped listing: this run's request budget is used up")
        self.stats["new_reports"] += len(new_reports)
        if cursor <= HISTORY_START:
            return f"complete - listed back to {HISTORY_START:%m/%Y} ({len(new_reports)} new this run)"
        return (f"listed {count(months, 'more month')}, back to {cursor:%m/%Y}: "
                f"{count(len(new_reports), 'new report')} ({plain_kinds(new_reports)})")

    def read_reports(self):
        """Step 5: download and read electronic reports, newest first, within the budget."""
        queue = ReportQueue(self.reports, self.budget, self.site.get_report, self.reader, self.stats, self.note,
                            failures_before_stop=FAILURES_BEFORE_STOP)
        return queue.read()

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
            self.history = iso_date(self.bookmarks.get("history_listed_back_to"))
        except DatabaseError:
            pass
        return totals

    def outcome(self, totals):
        stats = self.stats
        if (stats["new_reports"] == 0 and stats["reports_read"] == 0 and totals
                and totals["to_read"] == 0 and self.history_complete):
            return "- no new reports, everything is up to date"
        outcome = (f"- {count(stats['new_reports'], 'new report')}, {stats['reports_read']} read "
                   f"({count(stats['transactions_saved'], 'transaction')} saved)")
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
