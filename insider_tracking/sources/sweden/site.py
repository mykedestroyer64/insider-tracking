"""
site.py - talks to Finansinspektionen's register site (marknadssok.fi.se).

FI says it may limit heavy users of the register, so this client is gentle: it waits 2-3
seconds between requests, sends only a few per run (the budget), reads the site's robots.txt
rules first (and keeps to the delay they ask for), and stops if the site refuses it.
"""

from dataclasses import dataclass
from datetime import date
from urllib import robotparser
from urllib.parse import quote

from ...core.errors import SiteStop
from ...core.status import BLOCKED
from ...web.checks import BlockPageCheck, unexpected
from ...web.pacing import Pacing

PACING = Pacing(pause=(2.0, 3.0), retry_extra=10, timeout=(10, 120), slow_down_wait=30)
WHO_SITE = "Finansinspektionen's site"
WHO_REGISTER = "Finansinspektionen's register"


@dataclass(frozen=True)
class FiEndpoints:
    base: str = "https://marknadssok.fi.se"

    @property
    def robots(self):
        return self.base + "/robots.txt"

    @property
    def search_page(self):
        return self.base + "/Publiceringsklient/en-GB/Search/Start/Insyn"

    @property
    def export(self):
        """The register's "Export" button: a semicolon-separated UTF-16 text file of the search
        results, here filtered by publication date (YYYY-MM-DD, both days included)."""
        return (self.base + "/Publiceringsklient/en-GB/Search/Search?SearchFunctionType=Insyn"
                "&Publiceringsdatum.From={start:%Y-%m-%d}&Publiceringsdatum.To={end:%Y-%m-%d}&button=export")


def register_search_url(person, issuer, trade_date, endpoints=FiEndpoints()):
    """A link to the register's own search, showing this person's notifications for the company."""
    url = (endpoints.base + "/Publiceringsklient/en-GB/Search/Search?SearchFunctionType=Insyn"
           f"&Utgivare={quote(issuer or '')}&PersonILedandeSt%C3%A4llningNamn={quote(person or '')}")
    if trade_date:
        url += f"&Transaktionsdatum.From={trade_date:%Y-%m-%d}&Transaktionsdatum.To={trade_date:%Y-%m-%d}"
    return url + "&button=search"


class FiRegister:
    def __init__(self, client, endpoints=FiEndpoints(), check=None):
        self.client = client
        self.endpoints = endpoints
        self.check = check or BlockPageCheck()

    def robots_allow(self):
        """Read robots.txt: a note if scripts may read the register, or a SiteStop if not."""
        response = self.client.get(self.endpoints.robots, check=self.check, ok_codes=(404,), who=WHO_SITE)
        if response.status_code == 404:
            return "no robots.txt (no rules given)"
        parser = robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        sample = self.endpoints.export.format(start=date(2026, 1, 1), end=date(2026, 1, 2))
        if not (parser.can_fetch("*", self.endpoints.search_page) and parser.can_fetch("*", sample)):
            raise SiteStop(BLOCKED, "robots.txt says scripts may not read the register", response)
        delay = parser.crawl_delay("*")
        self.client.slow_down_to(delay)
        return "robots.txt allows the register" + (f", asks for {delay} s between requests" if delay else "")

    def export(self, start, end):
        """The register's export for notifications published from `start` to `end` (the answer)."""
        response = self.client.get(self.endpoints.export.format(start=start, end=end), check=self.check,
                                   who=WHO_REGISTER)
        content_type = response.headers.get("Content-Type", "")
        if "html" in content_type and b"<html" in response.content[:4000].lower():
            raise unexpected(response, "the export came back as a web page, not a data file")
        return response
