"""
secdays.py - asks EDGAR (3 requests) which daily lists of filings exist in a quarter, and what
asking for a missing one returns. It showed that a missing list answers "403 Access Denied",
just like a refusal, which is why the SEC collector asks only for the days EDGAR lists.
Notes only; the exit code is always 0.
"""

import re

from ..core.errors import SiteStop
from ..core.identity import user_agent
from ..runs.notes import Notes
from ..runs.settings import Settings
from ..web.client import PoliteClient, new_session
from ..web.pacing import Pacing

VERSION = "0.1"
QUARTER = "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/"
DAYS = ("20261012", "20261013")       # Columbus Day (EDGAR closed: no list) and the day after
PACING = Pacing(pause=(2.0, 2.0), retry_extra=0, timeout=(30, 30))


def page_start(text):
    """The first words of a page, without its HTML tags."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text[:600])).strip()[:200]


class EdgarDays:
    """client: a PoliteClient; note(text) posts a note."""

    def __init__(self, client, note, quarter=QUARTER, days=DAYS):
        self.client = client
        self.note = note
        self.quarter = quarter
        self.days = days

    def run(self):
        try:
            self.listing()
            for day in self.days:
                self.day_list(day)
        except SiteStop as stop:
            self.note(f"stopped: {stop.status} - {stop.detail}")

    def listing(self):
        response = self.client.get(self.quarter + "index.json")
        days = []
        try:
            days = sorted(item["name"] for item in response.json()["directory"]["item"]
                          if item.get("name", "").startswith("form."))
        except Exception as error:   # noqa: BLE001 - whatever came back, say so
            self.note(f"listing not readable: {error}")
        self.note(f"listing: HTTP {response.status_code}, {len(days)} form.*.idx files: "
                  + ", ".join(name[5:13] for name in days))

    def day_list(self, day):
        response = self.client.get(self.quarter + f"form.{day}.idx")
        self.note(f"form.{day}.idx: HTTP {response.status_code}, Content-Type {response.headers.get('Content-Type')}, "
                  f"{len(response.content):,} bytes, starts: {page_start(response.text)}")


class SecDaysCheck:
    """`check secdays`."""

    def __init__(self, quarter=QUARTER, days=DAYS):
        self.quarter = quarter
        self.days = days

    def run(self, argv, platform):
        settings = Settings.read(platform.environ)
        client = PoliteClient(new_session(user_agent(VERSION, settings.email)), PACING, who="the SEC",
                              sleep=platform.sleep)
        EdgarDays(client, Notes("SECDAYS check", limit=900), self.quarter, self.days).run()
        return 0


def command():
    """`python -m insider_tracking check secdays`."""
    return SecDaysCheck()
