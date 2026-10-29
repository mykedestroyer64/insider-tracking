"""
collector.py - the Senate collector: saves senators' periodic transaction reports from eFD.
This module is its definition (name, steps, wording) and its wiring: the real database
tables, eFD on the polite web client and the request budget, handed to the steps in run.py.

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

from ...core.identity import user_agent
from ...core.status import BLOCKED, CHANGED, DB_ERROR, ERROR, SITE_DOWN
from ...db.congress import CongressReports
from ...db.state import StateStore
from ...runs.command import CollectorCommand
from ...runs.definition import CollectorDefinition
from ...web.client import PoliteClient
from ...web.pacing import RequestBudget
from .parsing import parse_report_page
from .run import PageReader, SenateRun
from .site import PACING, EfdEndpoints, EfdSite

VERSION = "0.2"

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
            session = context.new_session(user_agent(VERSION, context.settings.email))
            return EfdSite(PoliteClient(session, self.pacing, budget, sleep=context.sleep), self.endpoints)

        reports = CongressReports(context.db, "senate")
        return SenateRun(reports=reports, bookmarks=StateStore(context.db), open_site=open_site, budget=budget,
                         reader=PageReader(reports, self.read_page), today=context.today, note=context.console.note)


def command():
    """`python -m insider_tracking collect senate`."""
    return CollectorCommand(SenateSource())
