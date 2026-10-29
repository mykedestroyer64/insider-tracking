"""
site.py - talks to the US Senate financial disclosure site (eFD).

eFD shows a usage agreement first; a visitor accepts it once per session (and again only if
the session runs out, at most a few times per run). Then the search answers with JSON and
each electronic report is a web page with a transactions table.
"""

from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ...core.errors import SiteStop
from ...core.status import CHANGED
from ...web.checks import BlockPageCheck, unexpected
from ...web.describe import http_codes
from ...web.pacing import Pacing

PACING = Pacing(pause=(2.0, 3.0), retry_extra=5, timeout=(10, 30))
PTR_TYPE = "[11]"      # report type 11 = Periodic Transaction Report
PAGE_SIZE = 100        # results per search request
MAX_AGREEMENTS = 5     # times per run we may accept the agreement (first time + 4 renewals)


@dataclass(frozen=True)
class EfdEndpoints:
    base: str = "https://efdsearch.senate.gov"

    @property
    def home(self):
        return self.base + "/search/home/"          # page with the usage agreement

    @property
    def search(self):
        return self.base + "/search/"               # search page, after the agreement

    @property
    def data(self):
        return self.base + "/search/report/data/"   # search results, as JSON


def search_form(date_from, date_to, start, length, token):
    """The search page's own form: PTRs received between two dates, every kind of filer."""
    return {
        "start": str(start),
        "length": str(length),
        "report_types": PTR_TYPE,
        "filer_types": "[]",                    # [] = every kind of filer
        "submitted_start_date": f"{date_from:%m/%d/%Y} 00:00:00",
        "submitted_end_date": f"{date_to:%m/%d/%Y} 00:00:00",
        "candidate_state": "",
        "senator_state": "",
        "office_id": "",
        "first_name": "",
        "last_name": "",
        "csrfmiddlewaretoken": token,
    }


def sent_back_to_agreement(response):
    return urlparse(response.url).path.startswith("/search/home")


class EfdSite:
    """One visitor session on eFD (client: a web.client.PoliteClient with the run's budget)."""

    def __init__(self, client, endpoints=EfdEndpoints(), check=None):
        self.client = client
        self.endpoints = endpoints
        self.check = check or BlockPageCheck()
        self.agreements = 0
        self.token = None

    @property
    def base_url(self):
        return self.endpoints.base

    # ------------------------------------------------------------ agreement

    def home_page(self):
        return self.client.get(self.endpoints.home, check=self.check)

    def token_on(self, home_page):
        """The form's security token (CSRF): in a form field, or else in a cookie."""
        page = BeautifulSoup(home_page.text, "html.parser")
        field = page.find("input", attrs={"name": "csrfmiddlewaretoken"})
        return (str(field.get("value") or "") if field else "") or self.client.cookie("csrftoken", "csrf")

    def agreement_answer(self, token):
        """Send the usage agreement's form, accepted (the answer as it came)."""
        return self.client.post(self.endpoints.home, check=self.check,
                                data={"csrfmiddlewaretoken": token, "prohibition_agreement": "1"},
                                headers={"Referer": self.endpoints.home})

    def post_agreement(self, token):
        """Accept the usage agreement; the site should send us on to /search/."""
        response = self.agreement_answer(token)
        page = BeautifulSoup(response.text, "html.parser")
        if page.find("input", attrs={"name": "prohibition_agreement"}):
            raise unexpected(response, "the agreement page came back, so it was not accepted")
        if urlparse(response.url).path != "/search/":
            raise unexpected(response, "expected to land on /search/ after the agreement, "
                                       f"but got {urlparse(response.url).path}")
        return response

    def open(self):
        """Read the token from the home page and accept the usage agreement.

        Returns the HTTP status codes seen, e.g. [200, 302, 200].
        """
        if self.agreements >= MAX_AGREEMENTS:
            raise SiteStop(CHANGED, "the session kept running out, even after accepting "
                                    f"the agreement {MAX_AGREEMENTS} times")
        self.agreements += 1
        response = self.home_page()
        codes = http_codes(response)
        token = self.token_on(response)
        if not token:
            raise unexpected(response, "no csrfmiddlewaretoken field and no csrftoken cookie "
                                       "on the home page")
        response = self.post_agreement(token)
        self.token = token
        return codes + http_codes(response)

    # --------------------------------------------------------------- search

    def search_page(self, date_from, date_to, start=0, length=PAGE_SIZE, token=None):
        """One search request (the answer as it came)."""
        form = search_form(date_from, date_to, start, length,
                           token or self.client.cookie("csrftoken", "csrf") or self.token)
        # X-Requested-With marks this as a data request, like the site's own search page
        return self.client.post(self.endpoints.data, check=self.check, data=form,
                                headers={"Referer": self.endpoints.search, "X-Requested-With": "XMLHttpRequest"})

    def search(self, date_from, date_to, start=0):
        """One search for PTRs received between two dates. Returns (rows, total)."""
        for attempt in (1, 2):   # the second try only happens after renewing the session
            response = self.search_page(date_from, date_to, start)
            if not sent_back_to_agreement(response):
                break
            if attempt == 2:
                raise unexpected(response, "the search keeps sending us back to the agreement page")
            self.open()          # the session ran out: accept the agreement again
        return read_search_answer(response)

    def search_all(self, date_from, date_to):
        """Every PTR received between two dates.

        If a date range has more results than one search returns, it is split in two halves
        (again and again if needed), so we never depend on the order in which the site sorts
        its results.
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

    def report(self, url, ok_codes=(404, 410)):
        """Download one report page (the answer as it came; a missing page is returned too)."""
        return self.client.get(url, check=self.check, ok_codes=ok_codes, headers={"Referer": self.endpoints.search})

    def get_report(self, url):
        """Download one report page, renewing the session if it ran out.

        A missing page (HTTP 404 or 410) is returned too, so the caller can mark just that
        report as failed instead of stopping the whole run.
        """
        for attempt in (1, 2):   # the second try only happens after renewing the session
            response = self.report(url)
            if not sent_back_to_agreement(response):
                return response
            if attempt == 2:
                raise unexpected(response, "the report link keeps sending us back to the agreement page")
            self.open()          # the session ran out: accept the agreement again


def search_results(response):
    """The search's JSON answer as it came. Raises SiteStop if it isn't what eFD sends."""
    try:
        results = response.json()
    except ValueError:
        raise unexpected(response, "the search answer was not JSON") from None
    if not isinstance(results, dict) or not isinstance(results.get("data"), list):
        raise unexpected(response, "the search JSON has no 'data' list")
    if results.get("result", "ok") != "ok":
        raise unexpected(response, f"the search JSON says result = {results.get('result')!r}")
    return results


def total_of(results):
    """How many reports match the search (more than one answer holds, when the site says so)."""
    try:
        return int(results.get("recordsFiltered", len(results["data"])))
    except (TypeError, ValueError):
        return len(results["data"])


def read_search_answer(response):
    """The search's JSON answer: (rows, total). Raises SiteStop if it isn't what eFD sends."""
    results = search_results(response)
    return results["data"], total_of(results)
