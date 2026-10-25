"""
sec_site.py - talks to the SEC's websites (sec.gov and EDGAR).

Used by collect_sec.py. The SEC allows scripts that identify themselves (a
User-Agent with a name and an email address) and stay well under 10 requests
a second. This session:
  - waits 0.3-0.6 seconds between requests (about 2 a second at most),
  - gives each request a time limit and retries a failed one at most once,
  - stops after a fixed number of requests per run (the "budget"),
  - and if the SEC refuses it, raises SiteStop and the collector stops.
"""

import html
import random
import re
import time
from urllib.parse import urlparse

import requests

from efd_site import (BLOCKED, CHANGED, OK, SITE_DOWN, EfdStop as SiteStop,  # noqa: F401
                      OutOfRequests, check_response, http_codes, short_error, unexpected)

SEC_URL = "https://www.sec.gov"
DATASETS_PAGE = SEC_URL + "/dera/data/form-345"                               # lists the quarterly files
DAILY_INDEX = SEC_URL + "/Archives/edgar/daily-index/{year}/QTR{quarter}/form.{day:%Y%m%d}.idx"
DAILY_LISTING = SEC_URL + "/Archives/edgar/daily-index/{year}/QTR{quarter}/index.json"   # the quarter's daily files
ARCHIVES = SEC_URL + "/Archives/"

TIMEOUT = (10, 120)    # seconds allowed to connect, and to receive the answer (the quarterly files are big)
PAUSE = (0.3, 0.6)     # seconds to wait between requests (picked at random)
RETRY_EXTRA_PAUSE = 10  # extra seconds to wait before the one retry

BLOCK_WORDS = ["undeclared automated tool", "request rate threshold exceeded", "request originates from an undeclared"]


class SecSession:
    """One visitor session on the SEC's sites, with a request budget for the run."""

    def __init__(self, user_agent, max_requests):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.session.headers["Accept-Encoding"] = "gzip, deflate"
        self.max_requests = max_requests
        self.requests_sent = 0

    @property
    def requests_left(self):
        return max(0, self.max_requests - self.requests_sent)

    def get(self, url, ok_codes=()):
        """GET one page or file: pause first, use a time limit, retry once if needed.

        Codes in ok_codes (such as 404 for a day without filings) are returned to
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
                response = self.session.get(url, timeout=TIMEOUT)
            except requests.RequestException as error:
                if attempt == 1:
                    continue
                raise SiteStop(SITE_DOWN, f"no answer from the SEC after 2 tries ({short_error(error)})") from None
            if response.status_code >= 500 and attempt == 1:
                continue
            if response.status_code == 429 and attempt == 1:
                time.sleep(30)          # asked to slow down: wait, then try once more
                continue
            if response.status_code not in ok_codes:
                self._check(response)
            return response
        raise SiteStop(SITE_DOWN, "no answer from the SEC after 2 tries")   # (not reached)

    @staticmethod
    def _check(response):
        try:
            content_type = response.headers.get("Content-Type", "")
            if "text" in content_type or "html" in content_type:
                lower = response.text[:4000].lower()
                if any(word in lower for word in BLOCK_WORDS):
                    raise SiteStop(BLOCKED, f"HTTP {response.status_code}: the SEC's page about automated tools "
                                            "(it wants a User-Agent with a name and email, and fewer requests)",
                                   response)
            check_response(response)
        except SiteStop as stop:
            # say which address was refused and what the refusal page says, so the verdict
            # line alone is enough to tell a one-off from a block of every request
            stop.detail = f"{stop.detail} - on {where(response.url)}{page_says(response)}"
            stop.args = (stop.detail,)
            raise


def where(url):
    """The part of an SEC address after the host: /Archives/edgar/daily-index/... or /Archives/edgar/data/..."""
    path = urlparse(url or "").path or "/"
    return path if len(path) <= 90 else path[:87] + "..."


def page_says(response):
    """The first words of an HTML refusal page, without its tags (empty for other answers)."""
    if "html" not in response.headers.get("Content-Type", "") and "<html" not in response.text[:500].lower():
        return ""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", response.text[:20000], flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    return f"; the page says: \"{text[:160]}\"" if text else ""
