"""
site.py - the European Central Bank's exchange-rate files.

Two files: the last 90 days as XML (small, read on normal runs) and the whole history since
1999 as a ZIP of one CSV (read once, to fill the table). Free to use with the ECB named as
the source.
"""

from dataclasses import dataclass

from ...core.errors import SiteStop
from ...core.status import CHANGED
from ...web.checks import BlockPageCheck
from .rates import RatesProblem, parse_history_zip, parse_xml

WHO = "the ECB"


@dataclass(frozen=True)
class EcbEndpoints:
    recent: str = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
    history: str = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"


class EcbSite:
    """client: the run's PoliteClient (the ECB's requests count towards the same budget)."""

    def __init__(self, client, endpoints=EcbEndpoints(), check=None):
        self.client = client
        self.endpoints = endpoints
        self.check = check or BlockPageCheck()

    def history_rows(self):
        """Every daily rate since 1999: [(day, currency, rate)]."""
        response = self.client.get(self.endpoints.history, check=self.check, who=WHO)
        try:
            return parse_history_zip(response.content)
        except RatesProblem as problem:
            raise SiteStop(CHANGED, f"the ECB's rates history could not be read: {problem}", response) from None

    def recent_rows(self):
        """The last 90 days' rates: [(day, currency, rate)]."""
        response = self.client.get(self.endpoints.recent, check=self.check, who=WHO)
        try:
            return parse_xml(response.text)
        except RatesProblem as problem:
            raise SiteStop(CHANGED, f"the ECB's recent rates could not be read: {problem}", response) from None
