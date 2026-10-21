"""
efd_site.py - talks to the US Senate financial disclosure site (eFD).

Used by collect.py. It behaves like one careful visitor:
  - it accepts the site's usage agreement once per session, and again only
    if the session runs out (at most a few times per run),
  - it waits 2-3 seconds between requests and gives each one a time limit,
  - it retries a failed request at most once,
  - it sends an honest User-Agent with a contact email,
  - it stops after a fixed number of requests per run (the "budget"),
  - and if the site blocks it, it raises EfdStop and the collector stops.
"""

import html
import random
import re
import time
from datetime import timedelta
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://efdsearch.senate.gov"
HOME_URL = BASE_URL + "/search/home/"          # page with the usage agreement
SEARCH_URL = BASE_URL + "/search/"             # search page, after the agreement
DATA_URL = BASE_URL + "/search/report/data/"   # search results, as JSON

PTR_TYPE = "[11]"      # report type 11 = Periodic Transaction Report
PAGE_SIZE = 100        # results per search request
TIMEOUT = (10, 30)     # seconds allowed to connect, and to wait for an answer
PAUSE = (2.0, 3.0)     # seconds to wait between requests (picked at random)
RETRY_EXTRA_PAUSE = 5  # extra seconds to wait before the one retry
MAX_AGREEMENTS = 5     # times per run we may accept the agreement (first time + 4 renewals)

# Statuses (the same ones the access check uses)
OK, BLOCKED, SITE_DOWN, CHANGED = "OK", "BLOCKED", "SITE_DOWN", "CHANGED"

MAINTENANCE_WORDS = ["maintenance", "temporarily unavailable",
                     "currently unavailable", "service unavailable"]
BOT_CHECK_WORDS = ["access denied", "sec-if-cpt", "bm-verify"]


class EfdStop(Exception):
    """The site blocked us, is down, or works differently: the collector must stop."""

    def __init__(self, status, detail, response=None, reference=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.response = response
        self.reference = reference   # Akamai "Reference #..." text, if any


class OutOfRequests(Exception):
    """This run has used all the requests it is allowed (not an error)."""


class EfdSession:
    """One visitor session on eFD, with a request budget for the run."""

    def __init__(self, user_agent, max_requests):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.max_requests = max_requests
        self.requests_sent = 0
        self.agreements = 0
        self.token = None

    @property
    def requests_left(self):
        return max(0, self.max_requests - self.requests_sent)

    # --------------------------------------------------------- basic request

    def _request(self, method, url, ok_codes=(), **options):
        """Send one request: pause first, use a time limit, retry once if needed.

        Error codes listed in ok_codes are returned to the caller instead of
        stopping the run (used for a single missing report page).
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
                response = self.session.request(method, url, timeout=TIMEOUT, **options)
            except requests.RequestException as error:
                if attempt == 1:
                    continue   # no answer (network problem): try once more
                raise EfdStop(SITE_DOWN, "no answer from the site after 2 tries "
                                         f"({short_error(error)})") from None
            if response.status_code >= 500 and attempt == 1:
                continue       # server error: try once more
            if response.status_code not in ok_codes:
                check_response(response)
            return response
        raise EfdStop(SITE_DOWN, "no answer from the site after 2 tries")   # (not reached)

    def _cookie(self, *names):
        for name in names:
            for cookie in self.session.cookies:
                if cookie.name == name:
                    return cookie.value
        return None

    # ------------------------------------------------------------ agreement

    def open(self):
        """Read the token from the home page and accept the usage agreement.

        Returns the HTTP status codes seen, e.g. [200, 302, 200].
        """
        if self.agreements >= MAX_AGREEMENTS:
            raise EfdStop(CHANGED, "the session kept running out, even after accepting "
                                   f"the agreement {MAX_AGREEMENTS} times")
        self.agreements += 1

        response = self._request("GET", HOME_URL)
        codes = http_codes(response)
        page = BeautifulSoup(response.text, "html.parser")
        field = page.find("input", attrs={"name": "csrfmiddlewaretoken"})
        token = (str(field.get("value") or "") if field else "") or self._cookie("csrftoken", "csrf")
        if not token:
            raise unexpected(response, "no csrfmiddlewaretoken field and no csrftoken cookie "
                                       "on the home page")

        response = self._request("POST", HOME_URL,
                                 data={"csrfmiddlewaretoken": token, "prohibition_agreement": "1"},
                                 headers={"Referer": HOME_URL})
        codes += http_codes(response)
        page = BeautifulSoup(response.text, "html.parser")
        if page.find("input", attrs={"name": "prohibition_agreement"}):
            raise unexpected(response, "the agreement page came back, so it was not accepted")
        if urlparse(response.url).path != "/search/":
            raise unexpected(response, "expected to land on /search/ after the agreement, "
                                       f"but got {urlparse(response.url).path}")
        self.token = token
        return codes

    def _sent_back_to_agreement(self, response):
        return urlparse(response.url).path.startswith("/search/home")

    # --------------------------------------------------------------- search

    def search(self, date_from, date_to, start=0):
        """One search for PTRs received between two dates. Returns (rows, total)."""
        form = {
            "start": str(start),
            "length": str(PAGE_SIZE),
            "report_types": PTR_TYPE,
            "filer_types": "[]",                    # [] = every kind of filer
            "submitted_start_date": f"{date_from:%m/%d/%Y} 00:00:00",
            "submitted_end_date": f"{date_to:%m/%d/%Y} 00:00:00",
            "candidate_state": "",
            "senator_state": "",
            "office_id": "",
            "first_name": "",
            "last_name": "",
        }
        for attempt in (1, 2):   # the second try only happens after renewing the session
            form["csrfmiddlewaretoken"] = self._cookie("csrftoken", "csrf") or self.token
            # X-Requested-With marks this as a data request, like the site's own search page
            response = self._request("POST", DATA_URL, data=form,
                                     headers={"Referer": SEARCH_URL,
                                              "X-Requested-With": "XMLHttpRequest"})
            if not self._sent_back_to_agreement(response):
                break
            if attempt == 2:
                raise unexpected(response, "the search keeps sending us back to the "
                                           "agreement page")
            self.open()          # the session ran out: accept the agreement again

        try:
            results = response.json()
        except ValueError:
            raise unexpected(response, "the search answer was not JSON") from None
        if not isinstance(results, dict) or not isinstance(results.get("data"), list):
            raise unexpected(response, "the search JSON has no 'data' list")
        if results.get("result", "ok") != "ok":
            raise unexpected(response, f"the search JSON says result = {results.get('result')!r}")
        rows = results["data"]
        try:
            total = int(results.get("recordsFiltered", len(rows)))
        except (TypeError, ValueError):
            total = len(rows)
        return rows, total

    def search_all(self, date_from, date_to):
        """Every PTR received between two dates.

        If a date range has more results than one search returns, it is split in
        two halves (again and again if needed), so we never depend on the order
        in which the site sorts its results.
        """
        rows, total = self.search(date_from, date_to)
        if total <= len(rows):
            return rows
        days = (date_to - date_from).days
        if days > 1:
            middle = date_from + timedelta(days=days // 2)
            return self.search_all(date_from, middle) + self.search_all(middle, date_to)
        # A single day with more results than one page: go through it page by page
        while len(rows) < total:
            more, total = self.search(date_from, date_to, start=len(rows))
            if not more:
                break
            rows += more
        return rows

    # -------------------------------------------------------------- reports

    def get_report(self, url):
        """Download one report page. Returns the response.

        A missing page (HTTP 404 or 410) is returned too, so the caller can mark
        just that report as failed instead of stopping the whole run.
        """
        for attempt in (1, 2):   # the second try only happens after renewing the session
            response = self._request("GET", url, ok_codes=(404, 410),
                                     headers={"Referer": SEARCH_URL})
            if not self._sent_back_to_agreement(response):
                return response
            if attempt == 2:
                raise unexpected(response, "the report link keeps sending us back to the "
                                           "agreement page")
            self.open()          # the session ran out: accept the agreement again


# --------------------------------------------------------------- helpers

def check_response(response):
    """Raise EfdStop if the answer is a block, an outage or an error page."""
    code = response.status_code
    text = html.unescape(response.text)   # Akamai writes "Reference&#32;&#35;..." in HTML codes
    if code == 403 and "CSRF verification failed" in text:
        raise EfdStop(CHANGED, "HTTP 403 'CSRF verification failed': the site did not accept "
                               "our form token, so its form has probably changed", response)
    reference = akamai_reference(text)
    if reference or code in (403, 429):
        if code == 429:
            what = "Too Many Requests"
        elif "Access Denied" in text:
            what = "Access Denied"
        else:
            what = "Forbidden"
        detail = f"HTTP {code} {what}" + (f", {reference}" if reference else "")
        raise EfdStop(BLOCKED, detail, response, reference)
    if code >= 500:
        raise EfdStop(SITE_DOWN, f"HTTP {code} server error, also after one retry", response)
    if code >= 400:
        raise EfdStop(CHANGED, f"HTTP {code} error page for {urlparse(response.url).path}",
                      response)


def akamai_reference(text):
    """If this is Akamai's "Access Denied" page, return its "Reference #..." text."""
    if "Access Denied" not in text:
        return None
    match = re.search(r"Reference\s*#\s*[\w.]+", text)
    return match.group(0) if match else None


def unexpected(response, problem):
    """A page loaded but did not look as expected: pick the status that fits."""
    lower = response.text.lower()
    if any(word in lower for word in MAINTENANCE_WORDS):
        return EfdStop(SITE_DOWN, f"{problem} (the page mentions maintenance)", response)
    if any(word in lower for word in BOT_CHECK_WORDS):
        return EfdStop(BLOCKED, f"{problem} (it looks like a bot-check page)", response)
    return EfdStop(CHANGED, problem, response)


def http_codes(response):
    """Status codes of a response and of any redirects before it, e.g. [302, 200]."""
    if response is None:
        return []
    return [r.status_code for r in response.history] + [response.status_code]


def short_error(error):
    """A short, readable description of a network error."""
    if isinstance(error, requests.Timeout):
        return "timed out"
    if isinstance(error, requests.exceptions.SSLError):
        return "secure connection failed"
    if isinstance(error, requests.ConnectionError):
        text = str(error).lower()
        if any(hint in text for hint in ("getaddrinfo", "name or service", "nodename",
                                         "name resolution")):
            return "the site's address could not be looked up"
        if "refused" in text:
            return "connection refused"
        if "reset" in text or "aborted" in text:
            return "connection cut off"
        return "could not connect"
    return " ".join(f"{type(error).__name__}: {error}".split())[:160]
