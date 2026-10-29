"""
Reading a queue of reports (sources/report_queue.py), with in-memory stand-ins for the
database, the site and the reader: no web server, no database. What is counted, when the run
stops (CHANGED after unreadable reports in a row, or the request budget), and the summary line.
"""
import sys

from insider_tracking.core.errors import OutOfRequests, SiteStop
from insider_tracking.core.status import CHANGED
from insider_tracking.sources.report_queue import ReadResult, ReportQueue

from .support.checks import Checks

check = Checks()


class Reports:
    """A chamber's unread reports, in memory."""

    def __init__(self, count):
        self.unread = [{"url": f"/r/{n}", "report_path": f"/r/{n}", "first_name": "Jane", "last_name": f"Doe{n}",
                        "date_received": "2026-10-01"} for n in range(count)]
        self.asked = []

    def to_read(self, limit, chamber="senate"):
        self.asked.append((limit, chamber))
        return self.unread[:limit]

    def counts(self, chamber="senate"):
        return {"to_read": len(self.unread)}


class Budget:
    def __init__(self, left):
        self.left = left
        self.sent = 0


class Reader:
    """Answers with the results it is given, one per report; reading takes the report off the list."""

    def __init__(self, reports, results):
        self.reports = reports
        self.results = list(results)

    def read(self, report, response):
        self.reports.unread.remove(report)
        return self.results.pop(0)


def download_within(budget):
    def download(url):
        if budget.left == 0:
            raise OutOfRequests()
        budget.left -= 1
        budget.sent += 1
        return f"page of {url}"
    return download


def queue(count, results, left=100, chamber="senate", stats=None):
    reports, budget, notes = Reports(count), Budget(left), []
    stats = stats if stats is not None else {"reports_read": 0, "reports_failed": 0, "transactions_saved": 0}
    reader = Reader(reports, results)
    return ReportQueue(reports, budget, download_within(budget), reader, stats, notes.append, chamber=chamber), stats, notes, reports


check.section("everything readable")
q, stats, notes, reports = queue(3, [ReadResult(transactions=2), ReadResult(transactions=5), ReadResult(transactions=1)])
detail = q.read()
print("  ", detail)
check("every report read, every transaction counted", stats == {"reports_read": 3, "reports_failed": 0,
                                                                  "transactions_saved": 8}, stats)
check("the summary line", detail == "read 3 reports, 8 transactions saved; 0 left to read", detail)
check("a note says how many will be read", notes[0] == "reading up to 3 reports, newest first", notes)

check.section("scans and failures")
q, stats, notes, reports = queue(4, [ReadResult(scanned=True), ReadResult(problem="no table"), ReadResult(transactions=3),
                                     ReadResult(problem="no table")], chamber="house",
                                 stats={"reports_read": 0, "reports_failed": 0, "transactions_saved": 0, "scanned": 0})
detail = q.read()
print("  ", detail)
check("scans, failures and reads counted apart", stats == {"reports_read": 1, "reports_failed": 2,
                                                             "transactions_saved": 3, "scanned": 1}, stats)
check("the summary names the scans and the failures",
      detail == "read 1 report, 3 transactions saved, 1 turned out to be scans, 2 could not be read; 0 left to read",
      detail)
check("each failure is a note with the report's filer and date",
      "could not read Jane Doe1 (2026-10-01): no table" in notes, notes)
check("the queue asks for the chamber's reports", reports.asked == [(100, "house")], reports.asked)
q, stats, notes, reports = queue(1, [ReadResult(scanned=True)])
q.read()
check("a scan counts even where the run keeps no scan count", stats.get("scanned") == 1, stats)

check.section("unreadable reports in a row")
q, stats, notes, reports = queue(5, [ReadResult(problem="changed")] * 5)
try:
    q.read()
    check("3 unreadable reports in a row stop the run", False)
except SiteStop as stop:
    check("3 unreadable reports in a row stop the run: CHANGED", stop.status == CHANGED
          and "3 reports in a row could not be read (last: changed)" in stop.detail, stop.detail)
check("the reports before the stop stay counted", stats["reports_failed"] == 3, stats)
q, stats, notes, reports = queue(5, [ReadResult(problem="x"), ReadResult(problem="x"), ReadResult(transactions=1),
                                     ReadResult(problem="x"), ReadResult(problem="x")])
q.read()
check("a readable report in between starts the count again", stats["reports_failed"] == 4, stats)

check.section("the request budget")
q, stats, notes, reports = queue(5, [ReadResult(transactions=1)] * 5, left=2)
detail = q.read()
check("only as many reports as requests are left", stats["reports_read"] == 2 and reports.asked == [(2, "senate")],
      (stats, reports.asked))
check("the rest stay for the next run", detail.endswith("; 3 left to read"), detail)
q, stats, notes, reports = queue(5, [], left=0)
detail = q.read()
check("no requests left: nothing is downloaded", detail == "no requests left in this run's budget; 5 left to read", detail)
q, stats, notes, reports = queue(0, [])
check("nothing to read", q.read() == "nothing to read - every electronic report found so far is read")
sys.exit(check.finish())
