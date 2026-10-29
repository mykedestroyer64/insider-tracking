"""
collector.py - the House collector: saves House members' trade reports in the database.
This module is its definition (name, steps, wording) and its wiring: the real database
tables, the House Clerk's site on the polite web client and the request budget, handed to
the steps in run.py.

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

from ...core.identity import user_agent
from ...core.status import BLOCKED, CHANGED, DB_ERROR, ERROR, SITE_DOWN
from ...db.congress import CongressReports
from ...db.state import StateStore
from ...runs.command import CollectorCommand
from ...runs.definition import CollectorDefinition
from ...web.client import PoliteClient
from ...web.pacing import RequestBudget
from .run import HouseRun, PdfReader
from .site import PACING, WHO, HouseEndpoints, HouseSite

VERSION = "0.3"

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


class HouseSource:
    definition = DEFINITION

    def __init__(self, endpoints=HouseEndpoints(), pacing=PACING):
        self.endpoints = endpoints
        self.pacing = pacing

    def start(self, context):
        budget = RequestBudget(context.max_requests)

        def open_site():
            session = context.new_session(user_agent(VERSION, context.settings.email))
            return HouseSite(PoliteClient(session, self.pacing, budget, who=WHO, sleep=context.sleep), self.endpoints)

        reports = CongressReports(context.db, "house")
        return HouseRun(reports=reports, bookmarks=StateStore(context.db), open_site=open_site, budget=budget,
                        report_url=self.endpoints.report, reader=PdfReader(reports), today=context.today,
                        note=context.console.note)


def command():
    """`python -m insider_tracking collect house`."""
    return CollectorCommand(HouseSource())
