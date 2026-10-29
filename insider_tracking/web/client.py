"""
client.py - the polite web client every collector and check uses.

It behaves like one careful visitor:
  - it waits between requests (Pacing) and gives each request a time limit,
  - it retries a failed request at most once (no answer, a server error, or "slow down"),
  - it sends an honest User-Agent (set by whoever creates the session),
  - it stops after a fixed number of requests per run (RequestBudget),
  - and it hands every answer to a ResponseCheck, which raises SiteStop when the site
    refused it, is down, or sent an error page: the run then stops. It never tries to get
    around a refusal.
"""

import random
import time

import requests

from ..core.errors import SiteStop
from ..core.status import SITE_DOWN
from .describe import short_error
from .pacing import RequestBudget


def new_session(user_agent, compressed=False):
    """A requests session that introduces itself (and accepts compressed answers, if asked)."""
    session = requests.Session()
    session.headers["User-Agent"] = user_agent
    if compressed:
        session.headers["Accept-Encoding"] = "gzip, deflate"
    return session


class PoliteClient:
    """One visitor's conversation with a site.

    session   a requests.Session (or anything with .request(), .headers and .cookies)
    pacing    the waits and time limits (web.pacing.Pacing)
    budget    the run's RequestBudget (shared when one run talks to two sites)
    who       how error messages name the site: "no answer from <who> after 2 tries"
    sleep     the function that waits (tests pass one that doesn't)
    """

    def __init__(self, session, pacing, budget=None, who="the site", sleep=time.sleep, pick=random.uniform):
        self.session = session
        self.pacing = pacing
        self.budget = budget if budget is not None else RequestBudget()
        self.who = who
        self._sleep = sleep
        self._pick = pick

    @property
    def requests_sent(self):
        return self.budget.sent

    @property
    def requests_left(self):
        return self.budget.left

    def slow_down_to(self, seconds):
        """Wait at least `seconds` between requests from now on (a site's crawl delay)."""
        self.pacing = self.pacing.at_least(seconds)

    def request(self, method, url, check=None, ok_codes=(), who=None, **options):
        """Send one request: pause first, use a time limit, retry once if needed.

        `check` (a ResponseCheck) raises SiteStop for refusals, outages and error pages;
        status codes listed in `ok_codes` are returned to the caller instead (a missing file).
        Without a check, every answer is returned as it came.
        """
        who = who or self.who
        for attempt in (1, 2):
            self.budget.check()
            if self.budget.sent > 0:
                wait = self._pick(*self.pacing.pause)
                if attempt == 2:
                    wait += self.pacing.retry_extra
                self._sleep(wait)
            self.budget.count()
            try:
                response = self.session.request(method, url, timeout=self.pacing.timeout, **options)
            except requests.RequestException as error:
                if attempt == 1:
                    continue   # no answer (network problem): try once more
                raise SiteStop(SITE_DOWN, f"no answer from {who} after 2 tries ({short_error(error)})") from None
            if response.status_code >= 500 and attempt == 1:
                continue       # server error: try once more
            if response.status_code == 429 and attempt == 1 and self.pacing.slow_down_wait is not None:
                self._sleep(self.pacing.slow_down_wait)   # asked to slow down: wait, then try once more
                continue
            if check is not None and response.status_code not in ok_codes:
                check(response)
            return response
        raise SiteStop(SITE_DOWN, f"no answer from {who} after 2 tries")   # (not reached)

    def get(self, url, **options):
        return self.request("GET", url, **options)

    def post(self, url, **options):
        return self.request("POST", url, **options)

    def cookie(self, *names):
        """The value of the first cookie found with one of these names, or None."""
        for name in names:
            for cookie in self.session.cookies:
                if cookie.name == name:
                    return cookie.value
        return None

    def cookie_values(self):
        return {cookie.name: cookie.value for cookie in self.session.cookies}
