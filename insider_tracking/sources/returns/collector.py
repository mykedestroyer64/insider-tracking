"""
collector.py - works out how each traded stock did afterwards, with Sharadar's prices.
This module is its definition (name, steps, wording) and its wiring: the real database
tables, Sharadar's API on the polite web client and the request budget, handed to the steps
in run.py.

GitHub Actions runs it (see .github/workflows/collect-returns.yml). Each run:
  1. connects to the database (and adds the trade_returns table the first time)
  2. gets SPY's prices (the S&P 500 fund) and lists the trades that need returns: stock
     trades in Congress reports (found by ticker) and company insiders' trades (found by the
     company's SEC number) whose 1-month, 6-month or 1-year period has ended since they were
     last worked out
  3. gets Sharadar's list of securities and finds the security each trade was in
  4. gets each security's prices and works out the returns, saving them as it goes
Prices are never stored or shown, only the returns (Sharadar's licence); the key is sent in
a request header, never in an address.

It needs a Sharadar subscription: the API key goes in the SHARADAR_API_KEY secret. Without it
the run does nothing and says so; that doesn't count as a failure.
"""

from ...core.identity import user_agent
from ...core.status import BLOCKED, CHANGED, DB_ERROR, ERROR, KEY_PROBLEM, SITE_DOWN
from ...db.returns import TradeReturns
from ...db.size import DatabaseSize
from ...runs.command import CollectorCommand
from ...runs.definition import CollectorDefinition
from ...runs.preconditions import DatabaseUrl, SharadarKey
from ...web.client import PoliteClient
from ...web.pacing import RequestBudget
from ..sharadar.site import PACING, WHO, SharadarApi, SharadarEndpoints
from .run import ReturnsRun

VERSION = "0.1"

DEFINITION = CollectorDefinition(
    key="returns",
    title="returns collector",
    version=VERSION,
    description="Work out how traded stocks did afterwards (Sharadar prices).",
    steps=("Connect to the database", "Find trades that need returns", "Find each trade's security",
           "Work out returns"),
    max_requests=2500,
    site=WHO,
    takes_email=False,
    label_width=36,
    shows_http_codes=False,
    keeps_problem_page=False,
    preconditions=(SharadarKey(), DatabaseUrl()),
    advice={
        KEY_PROBLEM: ["Sharadar didn't accept the API key. Check the SHARADAR_API_KEY secret (Settings >",
                      "Secrets and variables > Actions) against the key on sharadar.com/account, and that",
                      "the subscription is active."],
        BLOCKED: ["Sharadar refused the requests. Don't change anything; share this result."],
        SITE_DOWN: ["Sharadar had a server error. The next run tries again."],
        CHANGED: ["Sharadar's answers don't look the way the collector expects. Share this log."],
        DB_ERROR: ["Check the SUPABASE_DB_URL and SUPABASE_DB_PASSWORD secrets, and that the Supabase",
                   "project isn't paused."],
        ERROR: ["The collector hit a problem it didn't expect. Share this log."],
    },
)


class ReturnsSource:
    definition = DEFINITION

    def __init__(self, endpoints=SharadarEndpoints(), pacing=PACING):
        self.endpoints = endpoints
        self.pacing = pacing

    def start(self, context):
        budget = RequestBudget(context.max_requests)

        def open_api():
            session = context.new_session(user_agent(VERSION), compressed=True)
            client = PoliteClient(session, self.pacing, budget, who=WHO, sleep=context.sleep)
            return SharadarApi(client, context.settings.api_key, self.endpoints)

        return ReturnsRun(returns=TradeReturns(context.db), size=DatabaseSize(context.db), open_api=open_api,
                          budget=budget, today=context.started.date(), note=context.console.note)


def command():
    """`python -m insider_tracking collect returns`."""
    return CollectorCommand(ReturnsSource())
