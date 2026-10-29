"""
site.py - talks to the House Clerk's financial disclosure site.

The House site needs no agreement or login: its files are public downloads. A yearly list
of filings (XML) is asked for only if it changed since the last run: the site then answers
"304 Not Modified", which costs almost nothing.
"""

from dataclasses import dataclass

from ...web.checks import BlockPageCheck
from ...web.pacing import Pacing

PACING = Pacing(pause=(1.5, 2.5), retry_extra=5, timeout=(10, 60))
WHO = "the House site"


@dataclass(frozen=True)
class HouseEndpoints:
    base: str = "https://disclosures-clerk.house.gov"

    @property
    def yearly_list(self):
        return self.base + "/public_disc/financial-pdfs/{year}FD.xml"     # every filing of a year

    @property
    def report(self):
        return self.base + "/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"    # one trade report


class HouseSite:
    def __init__(self, client, endpoints=HouseEndpoints(), check=None):
        self.client = client
        self.endpoints = endpoints
        self.check = check or BlockPageCheck()

    def get_list(self, year, etag=None, modified=None):
        """One year's list of filings.

        Returns (response or None, status): status is "new" (the file, changed since last
        time), "unchanged" (304) or "missing" (404: no list for that year yet).
        """
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if modified:
            headers["If-Modified-Since"] = modified
        response = self.client.get(self.endpoints.yearly_list.format(year=year), check=self.check,
                                   ok_codes=(304, 404), headers=headers)
        if response.status_code == 304:
            return None, "unchanged"
        if response.status_code == 404:
            return None, "missing"
        return response, "new"

    def get_report(self, url):
        """Download one report (a PDF). A missing file (404 or 410) is returned too, so the
        caller can mark just that report as failed."""
        return self.client.get(url, check=self.check, ok_codes=(404, 410), headers={})
