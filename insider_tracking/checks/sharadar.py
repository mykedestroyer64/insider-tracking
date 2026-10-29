"""
sharadar.py - learns the format of Sharadar's API with the public test key Sharadar gave us
(it covers a small free sample, e.g. AAPL and SPY).

First the exact requests the returns collector sends (GitHub keeps only the first 10 notes of
a step), then the column lists of the tables (no key needed), the tickers-table row for AAPL,
and a short AAPL date range with only the date column. The notes show formats and counts,
never prices: the run notes of a public repository are public, and Sharadar's licence keeps
prices off the site. Delete the run afterwards anyway (it shows Sharadar's metadata for AAPL).
"""

import csv
import io
import json
from datetime import date

from ..core.errors import SiteStop
from ..core.identity import user_agent
from ..web.client import PoliteClient, new_session
from ..web.pacing import Pacing, RequestBudget
from ..sources.returns.calc import Series
from ..sources.sharadar.site import PACING, WHO, SharadarApi, SharadarEndpoints

TEST_KEY = "test-api-key"           # the public test key in Sharadar's email of 23 Oct 2026
VERSION = "0.1"
METADATA = ("ticker", "permaticker", "table", "category", "isdelisted", "exchange", "relatedtickers",
            "secfilings", "firstpricedate", "lastpricedate", "firstadded", "currency", "cusips")
ONE_A_SECOND = Pacing(pause=(1.0, 1.0), retry_extra=0, timeout=(60, 60))


def note(text):
    print("::notice title=SHARADAR check::" + text.replace("\n", " ")[:3000])


def rows_of(response):
    """(header, rows) of a CSV or JSON answer."""
    text = response.content.decode("utf-8-sig", errors="replace").lstrip()
    if text.startswith(("{", "[")):
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("data", "datatable", "rows"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return list(data[0]), [list(row.values()) for row in data]
        return [], []
    rows = list(csv.reader(io.StringIO(text)))
    return (rows[0], rows[1:]) if rows else ([], [])


class SharadarCheck:
    def __init__(self, endpoints=SharadarEndpoints(), sleep=None):
        self.endpoints = endpoints
        self.sleep = sleep

    def client(self, pacing, limit=None):
        options = {"sleep": self.sleep} if self.sleep else {}
        return PoliteClient(new_session(user_agent(VERSION), compressed=True), pacing, RequestBudget(limit),
                            who=WHO, **options)

    def collector_requests(self):
        """The exact requests the returns collector sends, with the test key: counts and dates only."""
        api = SharadarApi(self.client(PACING, limit=10), TEST_KEY, self.endpoints)
        for table, ticker in (("stocks", "AAPL"), ("funds", "SPY")):
            try:
                series = Series.from_csv(api.closes(table, ticker, date(2026, 9, 1), date(2026, 9, 30)))
                note(f"collector request {table}/{ticker} Sep 2026: {len(series)} closes, "
                     f"{series.days[0] if len(series) else '-'} to {series.last_day}")
            except SiteStop as stop:
                note(f"collector request {table}/{ticker}: {stop.status} - {stop.detail}")
        try:
            api.table_file("tickers")
            note("tickers file: downloaded (unexpected with the test key)")
        except SiteStop as stop:
            note(f"tickers file with the test key: {stop.status} - {stop.detail[:200]}")

    def formats(self):
        """The tables' columns, the AAPL tickers row and a date-only range (never prices)."""
        root = self.endpoints.api.rsplit("data/", 1)[0]
        client = self.client(ONE_A_SECOND)
        for table in ("tickers", "stocks", "funds", "actions"):
            response = client.get(root + f"schema/{table}")
            note(f"schema/{table}: HTTP {response.status_code}, {response.headers.get('Content-Type')}: "
                 + response.text[:1500])

        response = client.get(self.endpoints.api + "tickers", params={"ticker": "AAPL", "format": "csv"},
                              headers={"x-api-key": TEST_KEY})
        note(f"tickers row for AAPL: HTTP {response.status_code}")
        if response.status_code == 200:
            header, rows = rows_of(response)
            note(f"tickers columns: {', '.join(header)}; {len(rows)} rows")
            for row in rows[:3]:
                record = dict(zip(header, row))
                note("AAPL metadata: " + json.dumps({k: record.get(k) for k in METADATA if k in record}))
        else:
            note("answer: " + response.text[:300])

        response = client.get(self.endpoints.api + "stocks", params={"ticker": "AAPL", "from": "2026-09-01",
                                                                     "to": "2026-09-30", "fields": "date",
                                                                     "format": "csv"},
                              headers={"x-api-key": TEST_KEY})
        header, rows = rows_of(response) if response.status_code == 200 else ([], [])
        dates = sorted(row[0] for row in rows) if rows else []
        note(f"stocks AAPL Sep 2026, date column only, key in a header: HTTP {response.status_code}, "
             f"columns {header}, {len(rows)} rows" + (f", {dates[0]} to {dates[-1]}" if dates else "")
             + ("" if response.status_code == 200 else "; answer: " + response.text[:300]))

    def run(self, argv, platform):
        self.sleep = self.sleep or platform.sleep
        self.collector_requests()     # first: GitHub keeps only the first 10 notes of a step
        self.formats()
        return 0


def command():
    """`python -m insider_tracking check sharadar`."""
    return SharadarCheck()
