"""
site.py - talks to Sharadar's price API (api.sharadar.com) for the returns collector.

The key goes in a request header (x-api-key), never in an address, so it can't end up in a
log. Prices are only ever handed to the returns arithmetic: they are never printed, stored
or shown (Sharadar's licence).

Downloads of whole tables (the list of securities) are answered with a redirect to a
temporary file address on another server; that address is fetched without the key.
"""

import io
import zipfile
from dataclasses import dataclass

from ...core.errors import SiteStop
from ...core.status import BLOCKED, CHANGED, KEY_PROBLEM, SITE_DOWN
from ...web.pacing import Pacing

PACING = Pacing(pause=(0.3, 0.6), retry_extra=10, timeout=(10, 180), slow_down_wait=60)
WHO = "Sharadar"
ROW_LIMIT = 10000          # Sharadar answers at most this many rows per request


@dataclass(frozen=True)
class SharadarEndpoints:
    api: str = "https://api.sharadar.com/v1.0/data/"


class SharadarCheck:
    """Sharadar's answers: 200 (data) and 302 (a file to fetch) are fine; a 401/403 that talks
    about the key is a KEY_PROBLEM; other 403s and 429 are refusals (BLOCKED)."""

    def __init__(self, what):
        self.what = what      # "AAPL prices", "the tickers table": named in the stop's explanation

    def __call__(self, response):
        code = response.status_code
        if code in (200, 302):
            return
        text = response.text[:300].replace("\n", " ")
        if code in (401, 403) and ("key" in text.lower() or "tier" in text.lower() or "unauthor" in text.lower()):
            raise SiteStop(KEY_PROBLEM, f"HTTP {code} for {self.what}: Sharadar says: {text}", response)
        if code in (403, 429):
            raise SiteStop(BLOCKED, f"HTTP {code} for {self.what}: Sharadar refused the request: {text}", response)
        if code >= 500:
            raise SiteStop(SITE_DOWN, f"HTTP {code} server error for {self.what}, also after one retry", response)
        raise SiteStop(CHANGED, f"HTTP {code} for {self.what}: {text}", response)


class SharadarApi:
    def __init__(self, client, api_key, endpoints=SharadarEndpoints()):
        self.client = client
        self.api_key = api_key
        self.endpoints = endpoints

    def _get(self, url, what, params=None, with_key=True):
        headers = {"x-api-key": self.api_key} if with_key else {}
        return self.client.get(url, check=SharadarCheck(what), params=params, headers=headers,
                               allow_redirects=False)

    def table_file(self, table):
        """A whole table (Sharadar sends a redirect to a zipped CSV file): the CSV text."""
        response = self._get(self.endpoints.api + table, f"the {table} table", params={"years": "full"})
        if response.status_code == 302 and response.headers.get("Location"):
            response = self._get(response.headers["Location"], f"the {table} file", with_key=False)  # without the key
        content = response.content
        if content[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = [n for n in archive.namelist() if n.lower().endswith(".csv")] or archive.namelist()
                content = archive.read(names[0])
        text = content.decode("utf-8-sig", errors="replace")
        if not text.startswith("table,") and "permaticker" not in text[:300]:
            raise SiteStop(CHANGED, f"the {table} file doesn't look like Sharadar's table "
                                    f"(it starts: {text[:80]!r})", response)
        return text

    def closes(self, table, ticker, start, end):
        """Adjusted closes of one security from `start` to `end`, as CSV text with columns date,closeadj."""
        parts, skip = [], 0
        while True:
            params = {"ticker": ticker, "from": start.isoformat(), "to": end.isoformat(),
                      "fields": "date,closeadj", "format": "csv", "sort": "date.asc", "limit": ROW_LIMIT}
            if skip:
                params["skip"] = skip
            response = self._get(self.endpoints.api + table, f"{ticker} prices", params=params)
            text = response.content.decode("utf-8-sig", errors="replace")
            lines = text.splitlines()
            if not lines or not lines[0].lower().startswith("date"):
                raise SiteStop(CHANGED, f"the prices of {ticker} don't look like Sharadar's CSV "
                                        f"(they start: {text[:60]!r})", response)
            parts.append(lines if not parts else lines[1:])
            if len(lines) - 1 < ROW_LIMIT:
                break
            skip += ROW_LIMIT
        return "\n".join(line for part in parts for line in part)
