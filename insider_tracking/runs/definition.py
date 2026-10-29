"""
definition.py - everything about a collector that doesn't change from run to run: its name,
its steps, its wording and the advice it prints. The runner reads it; it holds no logic.
"""

from dataclasses import dataclass, field

from .preconditions import RequiredSettings


@dataclass(frozen=True)
class CollectorDefinition:
    key: str                   # the collector's name in collector_runs ("senate", "sec", ...)
    title: str                 # the first line says "insider-tracking <title> <version>"
    version: str               # also sent in the User-Agent
    description: str           # what --help says the command does
    steps: tuple               # every step's name, step 1 ("Connect to the database") first
    max_requests: int          # requests per run unless --max-requests says otherwise
    site: str | None = None    # "eFD": "At most 200 requests to eFD in this run"; None: "requests"
    pace_note: str = ""        # added to that line, e.g. ", 2-3 seconds apart"
    takes_email: bool = True   # has an --email option (the contact email for the User-Agent)
    label_width: int = 38      # how far the dots of the step lines go
    shows_http_codes: bool = True
    keeps_problem_page: bool = True   # saves the page that stopped a run in output/
    preconditions: tuple = (RequiredSettings(),)
    advice: dict = field(default_factory=dict)   # status -> lines printed at the end

    @property
    def _to_site(self):
        return f" to {self.site}" if self.site else ""

    def budget_line(self, max_requests):
        return f"At most {max_requests} requests{self._to_site} in this run{self.pace_note}"

    def requests_line(self, sent):
        return f"Requests{self._to_site} in this run: {sent}"

    def max_requests_help(self):
        return f"requests{self._to_site} in this run (default {self.max_requests})"
