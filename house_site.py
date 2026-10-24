"""
house_site.py - talks to the House Clerk's financial disclosure site.

Used by collect_house.py. It has the same manners as efd_site.py:
  - it waits 1.5-2.5 seconds between requests and gives each one a time limit,
  - it retries a failed request at most once,
  - it sends an honest User-Agent with a contact email,
  - it stops after a fixed number of requests per run (the "budget"),
  - it asks for a yearly list only if it changed since the last run
    (the site answers "304 Not Modified" otherwise, which costs almost nothing),
  - and if the site blocks it, it raises SiteStop and the collector stops.

The House site needs no agreement or login: its files are public downloads.
"""

import random
import time

import requests

from efd_site import (BLOCKED, CHANGED, OK, SITE_DOWN, EfdStop as SiteStop,  # noqa: F401
                      OutOfRequests, check_response, http_codes, short_error, unexpected)

BASE_URL = "https://disclosures-clerk.house.gov"
LIST_URL = BASE_URL + "/public_disc/financial-pdfs/{year}FD.xml"   # every filing of a year
REPORT_URL = BASE_URL + "/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"  # one trade report

TIMEOUT = (10, 60)     # seconds allowed to connect, and to receive the answer
PAUSE = (1.5, 2.5)     # seconds to wait between requests (picked at random)
RETRY_EXTRA_PAUSE = 5  # extra seconds to wait before the one retry


class HouseSession:
    """One visitor session on the House site, with a request budget for the run."""

    def __init__(self, user_agent, max_requests):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.max_requests = max_requests
        self.requests_sent = 0

    @property
    def requests_left(self):
        return max(0, self.max_requests - self.requests_sent)

    def _get(self, url, ok_codes=(), headers=None):
        """GET one file: pause first, use a time limit, retry once if needed.

        Codes listed in ok_codes (like 404 for a missing file) are returned to
        the caller instead of stopping the run.
        """
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
                response = self.session.get(url, timeout=TIMEOUT, headers=headers or {})
            except requests.RequestException as error:
                if attempt == 1:
                    continue   # no answer (network problem): try once more
                raise SiteStop(SITE_DOWN, "no answer from the House site after 2 tries "
                                          f"({short_error(error)})") from None
            if response.status_code >= 500 and attempt == 1:
                continue       # server error: try once more
            if response.status_code not in ok_codes:
                check_response(response)
            return response
        raise SiteStop(SITE_DOWN, "no answer from the House site after 2 tries")   # (not reached)

    def get_list(self, year, etag=None, modified=None):
        """One year's list of filings.

        Returns (response or None, status): status is "new" (the file, changed
        since last time), "unchanged" (304) or "missing" (404: no list for that
        year yet).
        """
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if modified:
            headers["If-Modified-Since"] = modified
        response = self._get(LIST_URL.format(year=year), ok_codes=(304, 404), headers=headers)
        if response.status_code == 304:
            return None, "unchanged"
        if response.status_code == 404:
            return None, "missing"
        return response, "new"

    def get_report(self, url):
        """Download one report (a PDF). A missing file (404 or 410) is returned too,
        so the caller can mark just that report as failed."""
        return self._get(url, ok_codes=(404, 410))
