"""
collector.py - the company-insider collector: officers' and directors' buys and sells (SEC Form 4).
This module is its definition (name, steps, wording) and its wiring: the real database
tables, the SEC's sites on the polite web client and the request budget, handed to the steps
in run.py.

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

from ...core.identity import user_agent
from ...core.status import BLOCKED, CHANGED, DB_ERROR, ERROR, SITE_DOWN
from ...db.insiders import InsiderFilings
from ...db.size import DatabaseSize
from ...db.state import StateStore
from ...runs.command import CollectorCommand
from ...runs.definition import CollectorDefinition
from ...web.client import PoliteClient
from ...web.pacing import RequestBudget
from .run import SecRun
from .site import PACING, WHO, SecEndpoints, SecSite

VERSION = "0.5"
FIRST_QUARTER = (2021, 1)  # how far back the quarterly files are loaded

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


class SecSource:
    definition = DEFINITION

    def __init__(self, endpoints=SecEndpoints(), pacing=PACING, first_quarter=FIRST_QUARTER):
        self.endpoints = endpoints
        self.pacing = pacing
        self.first_quarter = first_quarter

    def start(self, context):
        budget = RequestBudget(context.max_requests)

        def open_site():
            session = context.new_session(user_agent(VERSION, context.settings.email), compressed=True)
            return SecSite(PoliteClient(session, self.pacing, budget, who=WHO, sleep=context.sleep), self.endpoints)

        return SecRun(filings=InsiderFilings(context.db), bookmarks=StateStore(context.db),
                      size=DatabaseSize(context.db), open_site=open_site, budget=budget,
                      first_quarter=self.first_quarter, now=context.started, note=context.console.note)


def command():
    """`python -m insider_tracking collect sec`."""
    return CollectorCommand(SecSource())
