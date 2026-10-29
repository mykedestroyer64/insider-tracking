"""
The Sharadar probe (python -m insider_tracking check sharadar) against a stand-in of Sharadar's
API: it only posts notes; the test key travels only in a request header, and no price is ever
shown (Sharadar's licence).
"""
import json
import sys
from urllib.parse import parse_qs, urlparse

from insider_tracking.checks.sharadar import SharadarCheck
from insider_tracking.runs.platform import Platform
from insider_tracking.sources.sharadar.site import SharadarEndpoints

from .support.checks import Checks
from .support.commands import run as run_command
from .support.web import StandInHandler, serve

REQUESTS = []


class Handler(StandInHandler):
    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        REQUESTS.append((url.path, query, self.headers.get("x-api-key")))
        if url.path.startswith("/v1.0/schema/"):
            return self.reply(200, json.dumps({"table": url.path.rsplit("/", 1)[-1], "columns": ["ticker", "date"]}).encode(),
                              "application/json")
        if url.path == "/v1.0/data/tickers" and query.get("years") == "full":
            return self.reply(403, b'{"error": "This API key does not cover this table (tier)"}', "application/json")
        if url.path == "/v1.0/data/tickers":
            return self.reply(200, b"table,permaticker,ticker,name,isdelisted,category,secfilings\n"
                                   b"SEP,199059,AAPL,Apple Inc,N,Domestic Common Stock,"
                                   b"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193\n",
                              "text/csv")
        if url.path in ("/v1.0/data/stocks", "/v1.0/data/funds"):
            days = [f"2026-09-{d:02d}" for d in (1, 2, 3, 4, 8, 9, 10)]
            if query.get("fields") == "date":
                return self.reply(200, ("date\n" + "\n".join(days)).encode(), "text/csv")
            return self.reply(200, ("date,closeadj\n" + "\n".join(f"{d},{100 + i}" for i, d in enumerate(days))).encode(),
                              "text/csv")
        return self.reply(404, b"no", "text/plain")


BASE = serve(Handler)
check = Checks()
result = run_command(SharadarCheck(SharadarEndpoints(BASE + "/v1.0/data/")), [],
                     Platform(environ={}, sleep=lambda seconds: None))
out = result.out
print(out)
print("requests:", [(path, sorted(q)) for path, q, _ in REQUESTS])
check("exit code 0", result.code == 0, result.code)
check("the collector's own request is described (7 closes in September)",
      "collector request stocks/AAPL Sep 2026: 7 closes" in out, out[:300])
check("the tickers file the test key doesn't cover is reported", "KEY_PROBLEM" in out)
check("only the public test key was sent, and only in the header",
      sorted({key for _, _, key in REQUESTS if key}) == ["test-api-key"]
      and not any("api_key" in q or "test-api-key" in json.dumps(q) for _, q, _ in REQUESTS))
check("no price is ever shown", "100" not in out.replace("10000", ""))
check("every line is a note", all(line.startswith("::notice title=SHARADAR check::") for line in result.lines))
sys.exit(check.finish())
