"""
checks.py - tells a refusal, an outage or an error page from a normal answer.

A ResponseCheck is any callable that takes a response and raises SiteStop when the answer
means the run must stop; it returns nothing when the answer is fine. The client calls it on
every answer (web.client.PoliteClient), so each site can bring its own (sources/sec has one
that knows the SEC's page about automated tools; sources/sharadar one for its API's answers).

unexpected() is for pages that loaded fine but did not look as expected: it picks the status
that fits (a maintenance page, a bot check, or a site that changed).
"""

import html
import re
from typing import Protocol

from ..core.errors import SiteStop
from ..core.status import BLOCKED, CHANGED, SITE_DOWN
from .describe import path_of

MAINTENANCE_WORDS = ["maintenance", "temporarily unavailable", "currently unavailable", "service unavailable"]
BOT_CHECK_WORDS = ["access denied", "sec-if-cpt", "bm-verify"]


class ResponseCheck(Protocol):
    def __call__(self, response) -> None:
        """Raise SiteStop if the answer is a refusal, an outage or an error page."""


class BlockPageCheck:
    """HTTP 403/429 and Akamai's "Access Denied" page are refusals (BLOCKED), 5xx is an outage
    (SITE_DOWN), any other 4xx an error page (CHANGED). A 403 "CSRF verification failed" means
    a form changed, not a refusal.

    name_the_page: the error-page message names the page's path ("... error page for /x/").
    """

    def __init__(self, name_the_page=True):
        self.name_the_page = name_the_page

    def __call__(self, response):
        code = response.status_code
        text = html.unescape(response.text)   # Akamai writes "Reference&#32;&#35;..." in HTML codes
        if code == 403 and "CSRF verification failed" in text:
            raise SiteStop(CHANGED, "HTTP 403 'CSRF verification failed': the site did not accept "
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
            raise SiteStop(BLOCKED, detail, response, reference)
        if code >= 500:
            raise SiteStop(SITE_DOWN, f"HTTP {code} server error, also after one retry", response)
        if code >= 400:
            where = f" for {path_of(response.url)}" if self.name_the_page else ""
            raise SiteStop(CHANGED, f"HTTP {code} error page{where}", response)


def akamai_reference(text):
    """If this is Akamai's "Access Denied" page, return its "Reference #..." text."""
    if "Access Denied" not in text:
        return None
    match = re.search(r"Reference\s*#\s*[\w.]+", text)
    return match.group(0) if match else None


def unexpected(response, problem):
    """A page loaded but did not look as expected: the SiteStop with the status that fits."""
    lower = response.text.lower()
    if any(word in lower for word in MAINTENANCE_WORDS):
        return SiteStop(SITE_DOWN, f"{problem} (the page mentions maintenance)", response)
    if any(word in lower for word in BOT_CHECK_WORDS):
        return SiteStop(BLOCKED, f"{problem} (it looks like a bot-check page)", response)
    return SiteStop(CHANGED, problem, response)
