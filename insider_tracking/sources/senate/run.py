"""
run.py - the Senate collector's steps, once eFD is open (the wiring is in collector.py).

  2. open eFD and accept its usage agreement
  3. look for reports received in the last 60 days and store the new ones
  4. list older reports, two years per run, back to January 2012
  5. read electronic reports that haven't been read yet, newest first, and save their
     transactions (and the page)
Everything is saved as it goes: a run that stops early loses nothing, and the next run
carries on (the bookmarks recent_scan_day and history_listed_back_to).

Written against the interfaces in ../ports.py only: no database or web code in here.
"""

from datetime import date, timedelta
from typing import Callable, Protocol

from ...core.errors import DatabaseError, OutOfRequests, SiteStop
from ...core.status import CHANGED
from ...core.text import plural
from ...runs.steps import StepResult
from ..congress import plain_kinds
from ..ports import Bookmarks, Budget, EfdGateway, Note, ReportList, ReportQueueStore, ReportResults
from ..report_queue import ReadResult, ReportQueue, ReportReader
from .parsing import ReportProblem, parse_search_row

RECENT_DAYS = 60            # every run re-checks reports received in the last 60 days
HISTORY_START = date(2012, 1, 1)
MONTHS_PER_RUN = 24         # months of history to list per run
FAILURES_BEFORE_STOP = 3    # unreadable reports in a row before we assume the site changed


class SenateReports(ReportList, ReportQueueStore, ReportResults, Protocol):
    """Everything the run does with the Senate's reports (db.congress.CongressReports)."""


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


class PageReader:
    """Reads one downloaded Senate report page and saves its transactions (and the page).
    read_page(html) -> (report, transactions, odd rows), raising ReportProblem."""

    def __init__(self, reports: ReportResults, read_page: Callable):
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
    """One run's steps. reports: the Senate's reports (listed, queued and read); open_site()
    -> eFD; reader: saves what one report page holds."""

    def __init__(self, reports: SenateReports, bookmarks: Bookmarks, open_site: Callable[[], EfdGateway],
                 budget: Budget, reader: ReportReader, today: date, note: Note):
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
