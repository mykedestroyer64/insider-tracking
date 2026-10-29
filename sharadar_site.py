"""
sharadar_site.py - talks to Sharadar's price API (api.sharadar.com) for collect_returns.py.

The key goes in a request header (x-api-key), never in an address, so it can't end
up in a log. The session waits between requests, retries a failed one once, stops
after a fixed number of requests per run (the "budget") and stops the run if
Sharadar refuses it or the key doesn't work.

Downloads of whole tables (the list of securities) are answered with a redirect to a
temporary file address on another server; that address is fetched without the key.
"""

import io
import random
import time
import zipfile

import requests

from efd_site import (BLOCKED, CHANGED, OK, SITE_DOWN, EfdStop as SiteStop,  # noqa: F401
                      OutOfRequests, http_codes, short_error)

API = "https://api.sharadar.com/v1.0/data/"
TIMEOUT = (10, 180)        # seconds to connect, and to receive the answer
PAUSE = (0.3, 0.6)         # seconds to wait between requests (picked at random)
RETRY_EXTRA_PAUSE = 10     # extra seconds before the one retry
ROW_LIMIT = 10000          # Sharadar answers at most this many rows per request
KEY_PROBLEM = "KEY_PROBLEM"   # the key is missing, wrong or doesn't cover what was asked


class SharadarSession:
    def __init__(self, api_key, user_agent, max_requests):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.session.headers["Accept-Encoding"] = "gzip, deflate"
        self.api_key = api_key
        self.max_requests = max_requests
        self.requests_sent = 0

    @property
    def requests_left(self):
        return max(0, self.max_requests - self.requests_sent)

    def _send(self, url, params=None, with_key=True):
        for attempt in (1, 2):
            if self.requests_sent >= self.max_requests:
                raise OutOfRequests()
            if self.requests_sent > 0:
                wait = random.uniform(*PAUSE)
                if attempt == 2:
                    wait += RETRY_EXTRA_PAUSE
                time.sleep(wait)
            self.requests_sent += 1
            headers = {"x-api-key": self.api_key} if with_key else {}
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=TIMEOUT,
                                            allow_redirects=False)
            except requests.RequestException as error:
                if attempt == 1:
                    continue
                raise SiteStop(SITE_DOWN, f"no answer from Sharadar after 2 tries ({short_error(error)})") from None
            if response.status_code >= 500 and attempt == 1:
                continue
            if response.status_code == 429 and attempt == 1:
                time.sleep(60)          # asked to slow down: wait, then try once more
                continue
            return response
        raise SiteStop(SITE_DOWN, "no answer from Sharadar after 2 tries")   # (not reached)

    @staticmethod
    def _check(response, what):
        code = response.status_code
        if code in (200, 302):
            return
        text = response.text[:300].replace("\n", " ")
        if code in (401, 403) and ("key" in text.lower() or "tier" in text.lower() or "unauthor" in text.lower()):
            raise SiteStop(KEY_PROBLEM, f"HTTP {code} for {what}: Sharadar says: {text}", response)
        if code in (403, 429):
            raise SiteStop(BLOCKED, f"HTTP {code} for {what}: Sharadar refused the request: {text}", response)
        if code >= 500:
            raise SiteStop(SITE_DOWN, f"HTTP {code} server error for {what}, also after one retry", response)
        raise SiteStop(CHANGED, f"HTTP {code} for {what}: {text}", response)

    def table_file(self, table):
        """A whole table (Sharadar sends a redirect to a zipped CSV file): the CSV text."""
        response = self._send(API + table, params={"years": "full"})
        self._check(response, f"the {table} table")
        if response.status_code == 302 and response.headers.get("Location"):
            response = self._send(response.headers["Location"], with_key=False)   # the file, without the key
            self._check(response, f"the {table} file")
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
            response = self._send(API + table, params=params)
            self._check(response, f"{ticker} prices")
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
