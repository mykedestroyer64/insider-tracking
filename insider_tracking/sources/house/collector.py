"""
collector.py - the House collector: saves House members' trade reports in the database.

GitHub Actions runs it every 3 hours (see .github/workflows/collect-house.yml). Each run:
  1. connects to the database (and adds what the House needs, the first time)
  2. checks this year's and last year's lists of filings for new trade reports
  3. lists older years, three per run, back to 2013
  4. reads electronic reports that haven't been read yet (PDF files), newest first, and
     saves their transactions
Scanned paper reports are stored with status "not_read" and their link.

It sends at most 200 requests to the House site per run, 1.5-2.5 seconds apart, so the
history fills in over a few days. Everything is saved as it goes. Reading the PDFs needs the
pdfplumber library (it is in requirements.txt).
"""

from ...core.errors import OutOfRequests, SiteStop
from ...core.identity import user_agent
from ...core.status import BLOCKED, CHANGED, DB_ERROR, ERROR, SITE_DOWN
from ...core.text import plural
from ...db.congress import CongressReports
from ...db.connection import DatabaseError
from ...db.state import StateStore
from ...runs.command import CollectorCommand
from ...runs.definition import CollectorDefinition
from ...web.checks import unexpected
from ...web.client import PoliteClient, new_session
from ...web.pacing import RequestBudget
from ..congress import plain_kinds
from .parsing import ReportProblem, is_amendment, parse_list, parse_report
from .site import PACING, WHO, HouseEndpoints, HouseSite

VERSION = "0.3"
HISTORY_START_YEAR = 2013   # the first year with trade reports in the Clerk's lists
YEARS_PER_RUN = 3           # older yearly lists to read per run
FAILURES_BEFORE_STOP = 3    # unreadable reports in a row before we assume the site changed
CHAMBER = "house"

DEFINITION = CollectorDefinition(
    key="house",
    title="House collector",
    version=VERSION,
    description="Collect US House periodic transaction reports into the database.",
    steps=("Connect to the database", "Check for new reports", "List older years (history)", "Read reports"),
    max_requests=200,
    site=WHO,
    advice={
        BLOCKED: ["The House site refused the collector. The next scheduled run tries again in",
                  "3 hours. If several runs in a row are BLOCKED, stop there and share this result."],
        SITE_DOWN: ["The House site had a server error. The next scheduled run tries again in 3 hours."],
        CHANGED: ["A file did not look the way the collector expects, so the House site may work",
                  "differently now. Share this log so the collector can be updated."],
        DB_ERROR: ["Check the SUPABASE_DB_URL secret (it must be Supabase's Session pooler",
                   "string), the SUPABASE_DB_PASSWORD secret, and that the Supabase project",
                   "isn't paused."],
        ERROR: ["The collector hit a problem it didn't expect. Share this log."],
    },
)


def count(number, word):
    return plural(number, word, separators=False)


def scanned_kinds(reports):
    return plain_kinds(reports, "scanned", others=False)


class HouseSource:
    definition = DEFINITION

    def __init__(self, endpoints=HouseEndpoints(), pacing=PACING):
        self.endpoints = endpoints
        self.pacing = pacing

    def start(self, context):
        return HouseRun(context, self)


class HouseRun:
    def __init__(self, context, source):
        self.context = context
        self.source = source
        self.reports = CongressReports(context.db)
        self.state = StateStore(context.db)
        self.site = None
        self.history = None
        self.stats = {"new_reports": 0, "reports_read": 0, "reports_failed": 0, "scanned": 0,
                      "transactions_saved": 0}

    # ------------------------------------------------------------- the run

    def prepare(self):
        return f"tables ready, {self.reports.counts(CHAMBER)['reports']} House reports stored so far"

    def open(self):
        session = new_session(user_agent(VERSION, self.context.settings.email))
        client = PoliteClient(session, self.source.pacing, RequestBudget(self.context.max_requests), who=WHO,
                              sleep=self.context.sleep)
        self.site = HouseSite(client, self.source.endpoints)

    def steps(self):
        return [self.check_recent, self.list_history, self.read_reports]

    @property
    def requests_sent(self):
        return self.site.client.requests_sent if self.site else None

    def budget_used_up(self):
        return "this run's request budget is used up"

    # ----------------------------------------------------------- the steps

    def read_list(self, response, year):
        """The trade reports in a downloaded yearly list (stops the run if it can't be read)."""
        try:
            return parse_list(response.content, self.source.endpoints.report)
        except ReportProblem as problem:
            raise unexpected(response, f"the {year} list of filings: {problem}") from None

    def check_recent(self):
        """Step 2: this year's and last year's lists (filings early in a year can be in the last one)."""
        today = self.context.today
        parts = []
        for year in (today.year, today.year - 1):
            etag = self.state.get(f"house_list_etag_{year}")
            modified = self.state.get(f"house_list_modified_{year}")
            response, status = self.site.get_list(year, etag, modified)
            if status != "new":
                parts.append(f"{year}: {'no list yet' if status == 'missing' else 'unchanged'}")
                continue
            reports = self.read_list(response, year)
            new = self.reports.add(reports)
            self.stats["new_reports"] += len(new)
            for key, header in (("etag", "ETag"), ("modified", "Last-Modified")):
                if response.headers.get(header):
                    self.state.set(f"house_list_{key}_{year}", response.headers[header])
            part = f"{year}: {count(len(reports), 'trade report')}, {len(new)} new"
            if new:
                part += f" ({scanned_kinds(new)})"
            parts.append(part)
        if self.state.get("house_history_listed_back_to") is None:
            self.state.set("house_history_listed_back_to", str(today.year - 1))
        return "; ".join(parts)

    def list_history(self):
        """Step 3: the lists of older years, YEARS_PER_RUN per run, back to HISTORY_START_YEAR."""
        back_to = int(self.state.get("house_history_listed_back_to") or 0)
        if not back_to:
            return "nothing to do yet - the check for new reports hasn't finished"
        if back_to <= HISTORY_START_YEAR:
            return f"complete - every list back to {HISTORY_START_YEAR} is read"
        new_reports, years = [], []
        try:
            while back_to > HISTORY_START_YEAR and len(years) < YEARS_PER_RUN:
                year = back_to - 1
                response, status = self.site.get_list(year)
                if status == "new":
                    new_reports += self.reports.add(self.read_list(response, year))
                else:
                    self.context.console.note(f"no list for {year}")
                back_to = year
                self.state.set("house_history_listed_back_to", str(back_to))
                years.append(year)
        except OutOfRequests:
            self.context.console.note("stopped listing: this run's request budget is used up")
        self.stats["new_reports"] += len(new_reports)
        if back_to <= HISTORY_START_YEAR:
            return (f"complete - every list back to {HISTORY_START_YEAR} is read "
                    f"({len(new_reports)} new this run)")
        return (f"read the lists for {', '.join(str(y) for y in years) or 'no year'}: "
                f"{count(len(new_reports), 'new report')} ({scanned_kinds(new_reports)})")

    def read_reports(self):
        """Step 4: download and read electronic reports, newest first, within the budget."""
        stats, note = self.stats, self.context.console.note
        client = self.site.client
        if client.requests_left == 0:
            left = self.reports.counts(CHAMBER)["to_read"]
            return f"no requests left in this run's budget; {left} left to read"
        queue = self.reports.to_read(limit=client.requests_left, chamber=CHAMBER)
        if not queue:
            return "nothing to read - every electronic report found so far is read"
        note(f"reading up to {len(queue)} reports, newest first")
        in_a_row = 0
        for number, report in enumerate(queue, 1):
            try:
                response = self.site.get_report(report["url"])
            except OutOfRequests:
                break
            path, problem = report["report_path"], None
            if response.status_code in (404, 410):
                problem = f"file not found (HTTP {response.status_code})"
                self.reports.mark_failed(path, problem)
            elif not response.content.startswith(b"%PDF"):
                problem = "the file is not a PDF"
                self.reports.mark_failed(path, problem)
            else:
                try:
                    transactions, expected = parse_report(response.content)
                except ReportProblem as error:
                    if "no text" in str(error):     # a scan with an electronic-looking number
                        self.reports.mark_scanned(path, str(error))
                        stats["scanned"] += 1
                    else:
                        problem = str(error)
                        self.reports.mark_failed(path, problem)
                else:
                    remark = None
                    if len(transactions) < expected:
                        remark = f"{len(transactions)} of about {expected} transactions could be read"
                    self.reports.save_read(path, transactions, None, remark,
                                           is_amendment=is_amendment(transactions))
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

        left = self.reports.counts(CHAMBER)["to_read"]
        detail = (f"read {count(stats['reports_read'], 'report')}, "
                  f"{count(stats['transactions_saved'], 'transaction')} saved")
        if stats["scanned"]:
            detail += f", {stats['scanned']} turned out to be scans"
        if stats["reports_failed"]:
            detail += f", {stats['reports_failed']} could not be read"
        return detail + f"; {left} left to read"

    # ------------------------------------------------------- the summing up

    @property
    def history_complete(self):
        return self.history is not None and self.history <= HISTORY_START_YEAR

    def read_totals(self):
        totals = self.reports.counts(CHAMBER)
        try:
            history = self.state.get("house_history_listed_back_to")
            self.history = int(history) if history else None
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
            lines.append(f"Database now: {totals['reports']} House reports ({totals['read']} read, "
                         f"{totals['to_read']} to read, {totals['paper_not_read']} scanned not read, "
                         f"{totals['failed']} failed), {totals['transactions']} transactions")
        if self.history_complete:
            lines.append(f"History: complete back to {HISTORY_START_YEAR}")
        elif self.history:
            lines.append(f"History: lists read back to {self.history} (they go back to {HISTORY_START_YEAR})")
        return lines


def command():
    """`python -m insider_tracking collect house`."""
    return CollectorCommand(HouseSource())
