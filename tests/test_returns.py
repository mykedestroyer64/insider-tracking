"""
The returns collector (python -m insider_tracking collect returns) against a stand-in of
Sharadar's API (made-up securities with made-up prices) and a real PostgreSQL test database:
no key, a wrong key, matching trades to securities (former tickers, delisted companies, share
classes), the returns themselves, periods still running, a small budget, refusals.
"""
import csv
import io
import re
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from insider_tracking.db.database import Database
from insider_tracking.db.schema import ensure_schema
from insider_tracking.runs.command import CollectorCommand
from insider_tracking.sources.returns.collector import ReturnsSource
from insider_tracking.sources.sharadar import site as sharadar_site

from .support.checks import Checks
from .support.clocks import TestClock
from .support.commands import run as run_command
from .support.database import TestDatabase
from .support.platforms import SUPABASE_SETTINGS, test_platform
from .support.web import StandInHandler, serve

DB = TestDatabase("it_returns").fresh()
CLOCK = TestClock(datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc))   # the test moves it on by hand
GOOD_KEY = "good-key"
STATE = {"requests": [], "fail": None, "file_had_key": False}

# ---- made-up securities: (table, permaticker, ticker, isdelisted, category, related, first, last, cik, daily growth)
SECURITIES = [
    ("stocks", 199059, "AAPL", "N", "Domestic Common Stock", "", "1997-12-31", None, "0000320193", 1.0008),
    ("stocks", 194817, "META", "N", "Domestic Common Stock", "FB", "2012-05-18", None, "0001326801", 1.0005),
    ("stocks", 199999, "TWTR", "Y", "Domestic Common Stock", "", "2013-11-07", "2022-10-27", "0001418091", 0.9999),
    ("stocks", 1, "GOOGL", "N", "Domestic Common Stock Primary Class", "", "2004-08-19", None, "0001652044", 1.0006),
    ("stocks", 2, "GOOG", "N", "Domestic Common Stock Secondary Class", "", "2014-03-27", None, "0001652044", 1.0006),
    ("stocks", 10, "ABC1", "Y", "Domestic Common Stock", "ABC", "2005-01-03", "2015-06-30", "0000000111", 1.0002),
    ("stocks", 11, "ABC", "N", "Domestic Common Stock", "", "2018-01-02", None, "0000000222", 1.0004),
    ("stocks", 30, "XYZ", "N", "Domestic Common Stock", "", "2020-06-01", None, "0000000333", 1.0001),
    ("stocks", 20, "DUP", "N", "Domestic Common Stock", "", "2010-01-04", None, "0000000444", 1.0001),
    ("stocks", 21, "DUP", "N", "Domestic Common Stock", "", "2011-01-03", None, "0000000555", 1.0002),
    ("funds", 500, "SPY", "N", "ETF", "", "1993-01-29", None, "", 1.0003),
]
BY_TICKER = {}
for sec in SECURITIES:
    BY_TICKER.setdefault((sec[0], sec[2]), []).append(sec)


def market_last():
    day = CLOCK.when.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def price(sec, day):
    """Made-up adjusted close: grows by a fixed factor per calendar day; weekdays only, within its dates."""
    first = date.fromisoformat(sec[6])
    last = date.fromisoformat(sec[7]) if sec[7] else market_last()
    if day.weekday() >= 5 or day < first or day > last or day > market_last():
        return None
    return round(50.0 * sec[9] ** (day - date(2000, 1, 1)).days, 6)


def tickers_zip():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["table", "permaticker", "ticker", "name", "exchange", "isdelisted", "category", "cusips",
                     "relatedtickers", "firstpricedate", "lastpricedate", "secfilings"])
    for table, perm, ticker, delisted, category, related, first, last, cik, _ in SECURITIES:
        secfilings = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}" if cik else ""
        for t in (table, "fundamentals") if table == "stocks" else (table,):
            writer.writerow([t, perm, ticker, f"{ticker} Inc", "NYSE", delisted, category, "", related, first,
                             last or market_last().isoformat(), secfilings])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("SHARADAR_TICKERS.csv", out.getvalue())
    return buf.getvalue()


class Handler(StandInHandler):
    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        STATE["requests"].append(url.path + ("?" + url.query if url.query else ""))
        if url.path.startswith("/files/"):
            if self.headers.get("x-api-key"):
                STATE["file_had_key"] = True
            return self.reply(200, tickers_zip(), "application/zip")
        if STATE["fail"] == "429":
            return self.reply(429, b'{"error":"Too Many Requests"}', "application/json")
        if STATE["fail"] == "500":
            return self.reply(500, b"oops", "text/plain")
        if self.headers.get("x-api-key") != GOOD_KEY or "api_key" in query:
            return self.reply(401, b'{"error":"Unauthorized","description":"A valid API key is required."}',
                              "application/json")
        m = re.fullmatch(r"/v1\.0/data/(\w+)", url.path)
        if not m:
            return self.reply(404, b"no", "text/plain")
        table = m.group(1)
        if table == "tickers" and query.get("years") == "full":
            self.send_response(302)
            self.send_header("Location", f"{BASE}/files/SHARADAR_TICKERS.zip?sig=abc")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if table in ("stocks", "funds"):
            assert query.get("fields") == "date,closeadj" and query.get("format") == "csv", query
            secs = BY_TICKER.get((table, query["ticker"]), [])
            start, end = date.fromisoformat(query["from"]), date.fromisoformat(query["to"])
            rows = ["date,closeadj"]
            if secs:
                sec = secs[-1]
                day = start
                while day <= end:
                    value = price(sec, day)
                    if value is not None:
                        rows.append(f"{day.isoformat()},{value}")
                    day += timedelta(days=1)
            return self.reply(200, ("\n".join(rows) + "\n").encode(), "text/csv")
        return self.reply(404, b"no", "text/plain")


BASE = serve(Handler)
SOURCE = ReturnsSource(sharadar_site.SharadarEndpoints(BASE + "/v1.0/data/"), sharadar_site.PACING.without_waiting())
check = Checks()


def run(key=GOOD_KEY, max_requests=2500):
    environ = {**SUPABASE_SETTINGS, "GITHUB_ACTIONS": "true"}
    if key is not None:
        environ["SHARADAR_API_KEY"] = key
    result = run_command(CollectorCommand(SOURCE), ["--max-requests", str(max_requests)],
                         test_platform(DB, environ, clock=CLOCK))
    return result.code, result.out, result.last


def q(sql):
    return DB.query(sql)


def last_weekday(day):
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def expected(growth, day, period, stop=None):
    base = last_weekday(day)
    end = last_weekday(min(day + timedelta(days=period), stop) if stop else day + timedelta(days=period))
    return growth ** (end - base).days - 1


# ---- the database: a few Congress trades and insider trades
db = Database(DB.connect())
ensure_schema(db)
db.close()
congress = [  # (report, received, ticker, asset type, trade type, trade date)
    ("/r/1", "2019-03-20", "FB", "Stock", "purchase", "2019-03-01"),
    ("/r/2", "2023-05-20", "META", "Stock", "sale_full", "2023-05-02"),
    ("/r/3", "2022-06-20", "TWTR", "Stock", "purchase", "2022-06-01"),
    ("/r/4", "2014-03-20", "ABC", "Stock", "purchase", "2014-03-03"),
    ("/r/5", "2019-03-20", "ABC", "Stock", "sale_partial", "2019-03-01"),
    ("/r/6", "2015-03-20", "XYZ", "Stock", "purchase", "2015-03-02"),
    ("/r/7", "2019-03-20", "DUP", "Stock", "purchase", "2019-03-01"),
    ("/r/8", "2026-10-20", "AAPL", "Stock", "purchase", "2026-10-01"),
    ("/r/9", "2020-01-20", "AAPL", "Stock Option", "purchase", "2020-01-02"),     # an option: no return
    ("/r/10", "2020-01-20", "T WBD", "Stock", "exchange", "2020-01-02"),          # an exchange: no return
]
for path, received, ticker, asset, kind, day in congress:
    q(f"insert into reports (report_path, report_uuid, kind, status, url, date_received, chamber) values "
      f"('{path}', '{path}', 'electronic', 'read', 'https://x{path}', '{received}', 'senate')")
    q(f"insert into transactions (report_path, row_number, transaction_date, ticker, asset_type, trade_type, raw) "
      f"values ('{path}', 1, '{day}', '{ticker}', '{asset}', '{kind}', '{{}}')")
insiders = [  # (accession, cik, ticker, trade date)
    ("A-1", "320193", "AAPL", "2025-01-15"),
    ("A-2", "1652044", "GOOG", "2024-02-01"),
    ("A-3", "1652044", None, "2024-03-01"),
]
for acc, cik, ticker, day in insiders:
    tick = f"'{ticker}'" if ticker else "null"
    q(f"insert into insider_filings (accession, form, filing_date, company_cik, company_name, ticker, source) "
      f"values ('{acc}', '4', '{day}', '{cik}', 'Co', {tick}, 'test')")
    q(f"insert into insider_trades (accession, row_key, insider_cik, insider_name, trade_date, code, kind) "
      f"values ('{acc}', '1', '9', 'Person', '{day}', 'P', 'buy')")

print("== no key yet: nothing done, not a failure")
code, out, verdict = run(key=None)
print("   " + verdict)
check("NOT_RUN with exit 0", code == 0 and "NOT_RUN - no Sharadar key yet" in verdict, verdict)
check("no request sent", not STATE["requests"], STATE["requests"])

print("== a wrong key: KEY_PROBLEM")
code, out, verdict = run(key="wrong")
print("   " + verdict)
check("KEY_PROBLEM at step 2, exit 1", code == 1 and "KEY_PROBLEM at step 2" in verdict, verdict)

print("== first run")
STATE["requests"] = []
code, out, verdict = run()
print("   " + verdict)
print("\n".join("   " + l for l in out.splitlines() if l.startswith(("[", "Database", "Requests"))))
check("OK", code == 0 and verdict.startswith("VERDICT: OK"), out[-800:])
check("the key never travels in an address", not any("api_key" in r or GOOD_KEY in r for r in STATE["requests"]),
      STATE["requests"][:3])
check("the tickers file was fetched without the key", not STATE["file_had_key"])


def row(key, day):
    found = q(f"select security, r30, r182, r365, m30, m182, m365, coalesce(note, '') from trade_returns "
              f"where stock_key = '{key}' and trade_date = '{day}'")
    return found[0] if found else None


def close(a, b):
    return a not in (None, "") and abs(float(a) - b) < 1e-5


r = row("T:FB", "2019-03-01")
check("FB in 2019 is matched to META (former ticker)", r and r[0] == "META", r)
check("FB returns are right for 1 month, 6 months and 1 year",
      r and close(r[1], expected(1.0005, date(2019, 3, 1), 30)) and close(r[2], expected(1.0005, date(2019, 3, 1), 182))
      and close(r[3], expected(1.0005, date(2019, 3, 1), 365)), r)
check("SPY over the same days", r and close(r[4], expected(1.0003, date(2019, 3, 1), 30))
      and close(r[6], expected(1.0003, date(2019, 3, 1), 365)), r)
r = row("T:TWTR", "2022-06-01")
check("TWTR: 1 month normal, 6 months and 1 year up to its last day, with a note",
      r and close(r[1], expected(0.9999, date(2022, 6, 1), 30))
      and close(r[2], expected(0.9999, date(2022, 6, 1), 182, stop=date(2022, 10, 27)))
      and close(r[5], expected(1.0003, date(2022, 6, 1), 182, stop=date(2022, 10, 27)))
      and r[7] == "stopped trading 2022-10-27", r)
check("ABC in 2014 is the old company (ABC1), in 2019 the new one",
      (row("T:ABC", "2014-03-03") or [""])[0] == "ABC1" and (row("T:ABC", "2019-03-01") or [""])[0] == "ABC")
check("XYZ in 2015: no prices around the trade date", (row("T:XYZ", "2015-03-02") or [""] * 8)[7] == "no prices around the trade date")
check("DUP: ambiguous", (row("T:DUP", "2019-03-01") or [""] * 8)[7] == "ambiguous")
r = row("T:AAPL", "2026-10-01")
check("a trade from this month: periods still running stay empty", r and r[0] == "AAPL" and r[1] == "" and r[3] == "", r)
check("options and exchanges get no row", not row("T:AAPL", "2020-01-02") and not q("select 1 from trade_returns where stock_key in ('T:T', 'T:T WBD', 'T:WBD')"))
r = row("C:320193", "2025-01-15")
check("an insider trade is matched by the company's SEC number", r and r[0] == "AAPL"
      and close(r[3], expected(1.0008, date(2025, 1, 15), 365)), r)
check("Alphabet: the filing's ticker picks the share class (GOOG), else the primary class (GOOGL)",
      (row("C:1652044", "2024-02-01") or [""])[0] == "GOOG" and (row("C:1652044", "2024-03-01") or [""])[0] == "GOOGL")
prices_requests = [r for r in STATE["requests"] if "/data/stocks" in r or "/data/funds" in r]
check("one price request per security (SPY + 7)", len(prices_requests) == 8, prices_requests)

print("== second run: nothing new")
STATE["requests"] = []
code, out, verdict = run()
print("   " + verdict)
check("up to date, only SPY asked", "every return is up to date" in verdict and len(STATE["requests"]) == 1, STATE["requests"])

print("== 17 days later: this month's AAPL trade gets its 1-month return")
CLOCK.when = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
STATE["requests"] = []
code, out, verdict = run()
print("   " + verdict)
r = row("T:AAPL", "2026-10-01")
check("1 month worked out, 6 months still empty", r and close(r[1], expected(1.0008, date(2026, 10, 1), 30)) and r[2] == "", r)
check("only that security read again", len([x for x in STATE["requests"] if "/data/stocks" in x]) == 1, STATE["requests"])

print("== 40 days later: trades without a security are tried again")
CLOCK.when = datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc)
STATE["requests"] = []
code, out, verdict = run()
print("   " + verdict)
check("XYZ and DUP looked up again", "2 trades without one" in out, out[-900:])

print("== a small budget: saves what it did, says what is left")
q("delete from trade_returns")
STATE["requests"] = []
code, out, verdict = run(max_requests=5)
print("   " + verdict)
check("OK with securities left", code == 0 and "securities left for the next run" in out, out[-600:])
check("some rows saved", int(q("select count(*) from trade_returns")[0][0]) > 0)

print("== Sharadar asks to slow down twice: BLOCKED")
STATE["fail"] = "429"
code, out, verdict = run()
print("   " + verdict)
check("BLOCKED, exit 1", code == 1 and "BLOCKED at step 2" in verdict, verdict)
STATE["fail"] = "500"
code, out, verdict = run()
print("   " + verdict)
check("server error: SITE_DOWN", code == 1 and "SITE_DOWN at step 2" in verdict, verdict)
STATE["fail"] = None

check("runs recorded as returns", int(q("select count(*) from collector_runs where collector='returns'")[0][0]) >= 6)
sys.exit(check.finish())
