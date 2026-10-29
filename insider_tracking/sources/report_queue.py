"""
report_queue.py - reads a chamber's unread electronic reports, newest first, within the run's
request budget (the last step of the Senate and the House collectors).

Each report is downloaded and handed to the chamber's reader, which saves what the report
holds or says what was wrong with it. A few unreadable reports in a row mean the site has
probably changed: the run stops (CHANGED), and everything read before stays saved.
"""

from dataclasses import dataclass
from typing import Protocol

from ..core.errors import OutOfRequests, SiteStop
from ..core.status import CHANGED
from ..core.text import plural


@dataclass(frozen=True)
class ReadResult:
    problem: str | None = None    # why the report couldn't be read (it is marked failed)
    transactions: int = 0         # how many transactions were saved (when it was read)
    scanned: bool = False         # it turned out to be a scan with no text: kept as not read


class ReportReader(Protocol):
    def read(self, report, response) -> ReadResult:
        """Save what one downloaded report holds, or mark it failed; say which."""


class ReportQueue:
    """reports: a ReportStore; budget: the run's RequestBudget; download(url) -> response.
    stats: the run's counts (reports_read, reports_failed, transactions_saved, and scanned
    if the chamber has scans that look electronic)."""

    def __init__(self, reports, budget, download, reader, stats, note, chamber="senate", failures_before_stop=3):
        self.reports = reports
        self.budget = budget
        self.download = download
        self.reader = reader
        self.stats = stats
        self.note = note
        self.chamber = chamber
        self.failures_before_stop = failures_before_stop

    def read(self):
        stats, note = self.stats, self.note
        if self.budget.left == 0:
            left = self.reports.counts(self.chamber)["to_read"]
            return f"no requests left in this run's budget; {left} left to read"
        queue = self.reports.to_read(limit=self.budget.left, chamber=self.chamber)
        if not queue:
            return "nothing to read - every electronic report found so far is read"
        note(f"reading up to {len(queue)} reports, newest first")
        in_a_row = 0
        for number, report in enumerate(queue, 1):
            try:
                response = self.download(report["url"])
            except OutOfRequests:
                break
            result = self.reader.read(report, response)
            if result.scanned:
                stats["scanned"] += 1
            elif not result.problem:
                stats["reports_read"] += 1
                stats["transactions_saved"] += result.transactions
            if result.problem:
                stats["reports_failed"] += 1
                in_a_row += 1
                note(f"could not read {report['first_name']} {report['last_name']} "
                     f"({report['date_received']}): {result.problem}")
                if in_a_row >= self.failures_before_stop:
                    raise SiteStop(CHANGED, f"{in_a_row} reports in a row could not be read "
                                            f"(last: {result.problem})", response)
            else:
                in_a_row = 0
            if number % 25 == 0:
                note(f"{number} of {len(queue)} done")

        left = self.reports.counts(self.chamber)["to_read"]
        detail = (f"read {plural(stats['reports_read'], 'report', separators=False)}, "
                  f"{plural(stats['transactions_saved'], 'transaction', separators=False)} saved")
        if stats.get("scanned"):
            detail += f", {stats['scanned']} turned out to be scans"
        if stats["reports_failed"]:
            detail += f", {stats['reports_failed']} could not be read"
        return detail + f"; {left} left to read"
