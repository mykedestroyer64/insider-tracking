"""
site.py - talks to the SEC's websites (sec.gov and EDGAR).

The SEC allows scripts that identify themselves (a User-Agent with a name and an email
address) and stay well under 10 requests a second. This client waits 0.3-0.6 seconds between
requests (about 2 a second at most), and after "429 Too Many Requests" waits 30 seconds
before its one retry.
"""

import re
from dataclasses import dataclass

from ...core.errors import SiteStop
from ...core.status import BLOCKED
from ...web.checks import BlockPageCheck, unexpected
from ...web.describe import page_says, short_path
from ...web.pacing import Pacing

PACING = Pacing(pause=(0.3, 0.6), retry_extra=10, timeout=(10, 120), slow_down_wait=30)
WHO = "the SEC"
BLOCK_WORDS = ["undeclared automated tool", "request rate threshold exceeded", "request originates from an undeclared"]
QUARTERLY_LINK = re.compile(r'href="([^"]*?(\d{4})q([1-4])_form345\.zip)"', re.I)
DAILY_FILE = re.compile(r"form\.(\d{8})\.idx")


@dataclass(frozen=True)
class SecEndpoints:
    base: str = "https://www.sec.gov"

    @property
    def datasets_page(self):          # lists the quarterly files
        return self.base + "/dera/data/form-345"

    @property
    def daily_index(self):            # one day's list of filings
        return self.base + "/Archives/edgar/daily-index/{year}/QTR{quarter}/form.{day:%Y%m%d}.idx"

    @property
    def daily_listing(self):          # which days of a quarter have a list
        return self.base + "/Archives/edgar/daily-index/{year}/QTR{quarter}/index.json"

    @property
    def archives(self):
        return self.base + "/Archives/"


class SecCheck:
    """The SEC's page about automated tools is a refusal too; every stop says which address
    was refused and what the refusal page says, so the verdict line alone tells a one-off
    from a block of every request."""

    def __init__(self, inner=None):
        self.inner = inner or BlockPageCheck()

    def __call__(self, response):
        try:
            content_type = response.headers.get("Content-Type", "")
            if "text" in content_type or "html" in content_type:
                lower = response.text[:4000].lower()
                if any(word in lower for word in BLOCK_WORDS):
                    raise SiteStop(BLOCKED, f"HTTP {response.status_code}: the SEC's page about automated tools "
                                            "(it wants a User-Agent with a name and email, and fewer requests)",
                                   response)
            self.inner(response)
        except SiteStop as stop:
            raise stop.extended(f" - on {short_path(response.url)}{page_says(response)}") from None


class SecSite:
    def __init__(self, client, endpoints=SecEndpoints(), check=None):
        self.client = client
        self.endpoints = endpoints
        self.check = check or SecCheck()

    def get(self, url, ok_codes=()):
        return self.client.get(url, check=self.check, ok_codes=ok_codes)

    def quarterly_files(self):
        """The quarterly files listed on the SEC's data sets page: {(year, quarter): url}."""
        response = self.get(self.endpoints.datasets_page)
        links = {}
        for href, year, quarter in QUARTERLY_LINK.findall(response.text):
            links[(int(year), int(quarter))] = href if href.startswith("http") else self.endpoints.base + href
        if not links:
            raise unexpected(response, "no links to quarterly files (..._form345.zip) on the data sets page")
        return links

    def quarterly_file(self, url):
        """One quarterly ZIP file (the answer, whose content is the ZIP)."""
        return self.get(url)

    def listed_days(self, year, quarter):
        """The days of a quarter that have a daily list ('20261013', ...), from EDGAR's listing.

        A new quarter with no list yet answers 404: no days."""
        response = self.get(self.endpoints.daily_listing.format(year=year, quarter=quarter), ok_codes=(404,))
        if response.status_code == 404:
            return set()
        try:
            items = response.json()["directory"]["item"]
        except (ValueError, KeyError, TypeError):
            raise unexpected(response, f"the listing of daily lists for {year} Q{quarter} "
                                       "could not be read") from None
        return {m.group(1) for m in (DAILY_FILE.fullmatch(item.get("name", "")) for item in items) if m}

    def daily_index(self, year, quarter, day):
        """One day's list of filings (404 is returned: a holiday, or not published yet)."""
        return self.get(self.endpoints.daily_index.format(year=year, quarter=quarter, day=day), ok_codes=(404,))

    def filing(self, path):
        """One filing (404 is returned: listed but not available)."""
        return self.get(self.endpoints.archives + path, ok_codes=(404,))
