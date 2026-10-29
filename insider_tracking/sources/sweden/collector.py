"""
collector.py - the Swedish executives collector (Finansinspektionen's PDMR register).
This module is its definition (name, steps, wording) and its wiring: the real database
tables, the register and the ECB on one polite web client and the request budget, handed to
the steps in run.py.

GitHub Actions runs it every 3 hours (see .github/workflows/collect-sweden.yml). Each run:
  1. connects to the database (and adds the tables the first time)
  2. updates the euro exchange rates from the European Central Bank, so every trade in
     kronor (or dollars, or pounds) gets a euro figure next to it
  3. reads the register site's robots.txt, the rules it gives to scripts
  4. exports the notifications published in the last 45 days from the register (one
     request) and saves the buys and sells of shares in them
  5. loads the history, twelve months per run, back to July 2016 when the register started
Lines seen before are updated, never duplicated, so the windows can overlap and a
notification that is later revised or cancelled is marked as such.

It sends about 15 requests to Finansinspektionen per run, 2-3 seconds apart, with a
User-Agent that names the project and gives a contact email, and stops if the site refuses
it (no workarounds).
"""

from ...core.identity import user_agent
from ...core.status import BLOCKED, CHANGED, DB_ERROR, ERROR, SITE_DOWN
from ...db.europe import MarTrades
from ...db.fx import FxRates
from ...db.size import DatabaseSize
from ...db.state import StateStore
from ...runs.command import CollectorCommand
from ...runs.definition import CollectorDefinition
from ...web.client import PoliteClient
from ...web.pacing import RequestBudget
from ..ecb.site import EcbEndpoints, EcbSite
from .parsing import STOCKHOLM
from .run import SwedenRun
from .site import PACING, FiEndpoints, FiRegister

VERSION = "0.1"

DEFINITION = CollectorDefinition(
    key="sweden",
    title="Swedish executives collector",
    version=VERSION,
    description="Collect Swedish executives' buys and sells (Finansinspektionen's register) into the database.",
    steps=("Connect to the database", "Update euro exchange rates (ECB)", "Check the register's rules for scripts",
           "Export the last 45 days", "Load history (12 months per run)"),
    max_requests=60,
    label_width=42,
    advice={
        BLOCKED: ["Finansinspektionen's site refused the collector, or its robots.txt rules scripts out.",
                  "Do not add workarounds. The next scheduled run tries again in 3 hours; if several runs",
                  "in a row are BLOCKED, stop the schedule and share this result. FI can be asked by email",
                  "(finansinspektionen@fi.se) whether a daily export is acceptable."],
        SITE_DOWN: ["The site had a server error. The next scheduled run tries again in 3 hours."],
        CHANGED: ["A page or file did not look the way the collector expects, so the site may work",
                  "differently now. Share this log so the collector can be updated."],
        DB_ERROR: ["Check the SUPABASE_DB_URL secret (it must be Supabase's Session pooler string),",
                   "the SUPABASE_DB_PASSWORD secret, and that the Supabase project isn't paused."],
        ERROR: ["The collector hit a problem it didn't expect. Share this log."],
    },
)


class SwedenSource:
    definition = DEFINITION

    def __init__(self, endpoints=FiEndpoints(), ecb_endpoints=EcbEndpoints(), pacing=PACING):
        self.endpoints = endpoints
        self.ecb_endpoints = ecb_endpoints
        self.pacing = pacing

    def start(self, context):
        budget = RequestBudget(context.max_requests)

        def open_sites():
            """The register and the ECB, through one client (one budget, one pace)."""
            session = context.new_session(user_agent(VERSION, context.settings.email), compressed=True)
            client = PoliteClient(session, self.pacing, budget, sleep=context.sleep)
            return FiRegister(client, self.endpoints), EcbSite(client, self.ecb_endpoints)

        return SwedenRun(trades=MarTrades(context.db), fx=FxRates(context.db), bookmarks=StateStore(context.db),
                         size=DatabaseSize(context.db), open_sites=open_sites, budget=budget,
                         today=context.started.astimezone(STOCKHOLM).date(), computer_today=context.today,
                         note=context.console.note)


def command():
    """`python -m insider_tracking collect sweden`."""
    return CollectorCommand(SwedenSource())
