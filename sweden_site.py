"""
sweden_site.py - talks to Finansinspektionen's register site (marknadssok.fi.se)
and to the European Central Bank's exchange-rate files.

Used by collect_sweden.py. FI says it may limit heavy users of the register, so
this session is gentle: it waits 2-3 seconds between requests, sends only a
few per run (the "budget"), reads the site's robots.txt rules first, and if
the site refuses it, raises SiteStop and the collector stops.
"""

import random
import time
from datetime import date
from urllib import robotparser

import requests

from efd_site import (BLOCKED, CHANGED, OK, SITE_DOWN, EfdStop as SiteStop,  # noqa: F401
                      OutOfRequests, check_response, http_codes, short_error, unexpected)

FI_URL = "https://marknadssok.fi.se"
ROBOTS = FI_URL + "/robots.txt"
SEARCH_PAGE = FI_URL + "/Publiceringsklient/en-GB/Search/Start/Insyn"
# The register's "Export" button: a semicolon-separated UTF-16 text file of the search
# results, here filtered by publication date (YYYY-MM-DD, both days included).
EXPORT = (FI_URL + "/Publiceringsklient/en-GB/Search/Search?SearchFunctionType=Insyn"
          "&Publiceringsdatum.From={start:%Y-%m-%d}&Publiceringsdatum.To={end:%Y-%m-%d}&button=export")

# The ECB's euro reference rates: one XML file with the last 90 days, one ZIP with
# the whole history since 1999. Free to use with the ECB named as the source.
ECB_RECENT = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
ECB_HISTORY = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"

TIMEOUT = (10, 120)     # seconds allowed to connect, and to receive the answer
PAUSE = (2.0, 3.0)      # seconds to wait between requests (picked at random)
RETRY_EXTRA_PAUSE = 10  # extra seconds to wait before the one retry


class FiSession:
    """One visitor session, with a request budget for the run."""

    def __init__(self, user_agent, max_requests):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.session.headers["Accept-Encoding"] = "gzip, deflate"
        self.max_requests = max_requests
        self.requests_sent = 0

    @property
    def requests_left(self):
        return max(0, self.max_requests - self.requests_sent)

    def get(self, url, ok_codes=(), who="the site"):
        """GET one page or file: pause first, use a time limit, retry once if needed."""
        for attempt in (1, 2):
            if self.requests_sent >= self.max_requests:
                raise OutOfRequests()
            if self.requests_sent > 0:
                wait = random.uniform(*PAUSE)
                if attempt == 2:
                    wait += RETRY_EXTRA_PAUSE
                time.sleep(wait)
            self.requests_sent += 1
            try:
                response = self.session.get(url, timeout=TIMEOUT)
            except requests.RequestException as error:
                if attempt == 1:
                    continue
                raise SiteStop(SITE_DOWN, f"no answer from {who} after 2 tries ({short_error(error)})") from None
            if response.status_code >= 500 and attempt == 1:
                continue
            if response.status_code == 429 and attempt == 1:
                time.sleep(30)          # asked to slow down: wait, then try once more
                continue
            if response.status_code not in ok_codes:
                check_response(response)
            return response
        raise SiteStop(SITE_DOWN, f"no answer from {who} after 2 tries")   # (not reached)

    def robots_allow(self):
        """Read robots.txt: a note if scripts may read the register, or a SiteStop if not."""
        global PAUSE
        response = self.get(ROBOTS, ok_codes=(404,), who="Finansinspektionen's site")
        if response.status_code == 404:
            return "no robots.txt (no rules given)"
        parser = robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        sample = EXPORT.format(start=date(2026, 1, 1), end=date(2026, 1, 2))
        if not (parser.can_fetch("*", SEARCH_PAGE) and parser.can_fetch("*", sample)):
            raise SiteStop(BLOCKED, "robots.txt says scripts may not read the register", response)
        delay = parser.crawl_delay("*")
        if delay and delay > PAUSE[0]:
            PAUSE = (float(delay), float(delay) + 1.0)
        return "robots.txt allows the register" + (f", asks for {delay} s between requests" if delay else "")

    def export(self, start, end):
        """The register's export for notifications published from `start` to `end` (bytes)."""
        response = self.get(EXPORT.format(start=start, end=end), who="Finansinspektionen's register")
        content_type = response.headers.get("Content-Type", "")
        if "html" in content_type and b"<html" in response.content[:4000].lower():
            raise unexpected(response, "the export came back as a web page, not a data file")
        return response
