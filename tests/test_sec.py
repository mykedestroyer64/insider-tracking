"""
The SEC collector (python -m insider_tracking collect sec) against a stand-in SEC site and a
real PostgreSQL test database: made-up quarterly files, then the daily lists with real Form 4
filings (saved by the SEC access check: tests/fixtures/sec), a holiday, a refused day, a block.
"""
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from insider_tracking.runs.command import CollectorCommand
from insider_tracking.sources.sec import site as sec_site
from insider_tracking.sources.sec.collector import SecSource

from .sites.sec_files import quarter_zip
from .support.checks import Checks
from .support.clocks import TestClock
from .support.commands import run as run_command
from .support.database import TestDatabase
from .support.platforms import settings, test_platform
from .support.web import StandInHandler, serve

DB = TestDatabase("it_sec").fresh()
SAMPLES = Path(__file__).resolve().parent / "fixtures" / "sec"
FORM4 = {p.stem: p.read_text(encoding="utf-8", errors="replace") for p in sorted(SAMPLES.glob("*.txt"))}

# ---- quarterly files: tiny ones for 2026q3 and 2026q2 (newest first), and an "old" 2021q1
ZIPS = {"2026q3": quarter_zip("Q3", 60, "15-AUG-2026"), "2026q2": quarter_zip("Q2", 40, "15-MAY-2026"),
        "2021q1": quarter_zip("Q1", 20, "15-FEB-2021")}
PAGE = "".join(f'<a href="/files/data/{q}_form345.zip">{q}</a>' for q in ZIPS).encode()

# ---- daily lists: three business days after the quarter, with the real Form 4 samples
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)       # a Tuesday noon UTC
DAYS = {date(2026, 10, 1): list(FORM4)[:3], date(2026, 10, 2): list(FORM4)[3:5],
        date(2026, 10, 5): list(FORM4)[5:], date(2026, 10, 6): list(FORM4)[:2]}   # the 6th is today (not final)


def daily_index(day, accessions):
    lines = ["Description: Daily Index", "Last Data Received: x", "Comments: webmaster@sec.gov", "", "",
             "Form Type   Company Name    CIK   Date Filed  File Name", "-" * 80]
    for acc in accessions:
        lines.append(f"4           SOME COMPANY INC                                              1234567     {day:%Y%m%d}    edgar/data/1234567/{acc}.txt")
    lines.append(f"10-K        OTHER CO                                                      7654321     {day:%Y%m%d}    edgar/data/7654321/0000000000-26-000001.txt")
    return "\n".join(lines).encode()


STATE = {"requests": 0, "block": False, "filing_gets": 0, "listings": 0, "daily_gets": []}
REFUSED = set()      # days the stand-in lists but refuses (a real block)


class Handler(StandInHandler):
    def do_GET(self):
        STATE["requests"] += 1
        if STATE["block"]:
            return self.reply(403, b"<html>Your Request Originates from an Undeclared Automated Tool</html>", "text/html")
        if self.path == "/dera/data/form-345":
            return self.reply(200, PAGE, "text/html")
        m = re.fullmatch(r"/files/data/(\d{4}q[1-4])_form345\.zip", self.path)
        if m and m.group(1) in ZIPS:
            return self.reply(200, ZIPS[m.group(1)], "application/zip")
        m = re.fullmatch(r"/Archives/edgar/daily-index/(\d{4})/QTR(\d)/index\.json", self.path)
        if m:
            year, quarter = int(m.group(1)), int(m.group(2))
            items = [{"name": f"form.{d:%Y%m%d}.idx", "type": "file"} for d in sorted(set(DAYS) | REFUSED)
                     if d.year == year and (d.month - 1) // 3 + 1 == quarter]
            STATE["listings"] += 1
            return self.reply(200, json.dumps({"directory": {"item": items, "name": "x"}}).encode(), "application/json")
        m = re.fullmatch(r"/Archives/edgar/daily-index/\d{4}/QTR\d/form\.(\d{8})\.idx", self.path)
        if m:
            day = datetime.strptime(m.group(1), "%Y%m%d").date()
            STATE["daily_gets"].append(day)
            if day in REFUSED:
                return self.reply(403, b"<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY>Access Denied</BODY></HTML>", "text/html")
            if day in DAYS:
                return self.reply(200, daily_index(day, DAYS[day]), "text/plain")
            # like the SEC's storage: a file that doesn't exist answers "403 Access Denied" in XML
            return self.reply(403, b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>', "application/xml")
        m = re.fullmatch(r"/Archives/edgar/data/\d+/([\w-]+)\.txt", self.path)
        if m and m.group(1) in FORM4:
            STATE["filing_gets"] += 1
            return self.reply(200, FORM4[m.group(1)].encode("utf-8"), "text/plain")
        return self.reply(404, b"not found", "text/html")


BASE = serve(Handler)
CLOCK = TestClock(NOW)
SOURCE = SecSource(sec_site.SecEndpoints(BASE), sec_site.PACING.without_waiting(), first_quarter=(2021, 1))
PLATFORM = test_platform(DB, settings(), clock=CLOCK)
check = Checks()


def run(max_requests=1200):
    result = run_command(CollectorCommand(SOURCE), ["--max-requests", str(max_requests)], PLATFORM)
    return result.code, result.out, result.last


def q(sql):
    return DB.query(sql)


print("== first run: two newest quarters, then the daily lists")
code, out, verdict = run()
print("   " + verdict)
print("\n".join("   " + l for l in out.splitlines() if l.startswith(("[", "      ", "Database"))))
check("OK", code == 0 and verdict.startswith("VERDICT: OK"), verdict)
check("2026q3 and 2026q2 loaded, 2021q1 left", q("select value from collector_state where key='sec_quarters_done'") == [["2026q2,2026q3"]] and "1 quarterly file left" in out, out[-500:])
counts = q("select count(*), count(*) filter (where kind='buy'), count(*) filter (where kind='sell') from insider_trades where accession like 'Q%'")
check("quarterly trades saved: 100 buys and sells, no awards", counts == [["100", "50", "50"]], counts)
check("daily lists: 1 Oct, 2 Oct and 5 Oct finished, 6 Oct (today) not final",
      q("select value from collector_state where key='sec_daily_cursor'") == [["2026-10-05"]] and "3 days finished" in out, out[-400:])
daily = q("select source, count(*) from insider_filings where source like 'daily%' group by 1 order by 1")
print("   ", daily)
check("real Form 4 samples with buys/sells saved from the daily lists (AIR, ACU, AMT)", sum(int(r[1]) for r in daily) == 3, daily)
seen = json.loads(q("select value from collector_state where key='sec_daily_seen'")[0][0])
check("today's filings remembered as seen, not saved as done", seen["day"] == "2026-10-06" and len(seen["accessions"]) == 2, seen)
fetched_first = STATE["filing_gets"]

print("== second run: 2021q1 loads, today's list re-read without refetching")
code, out, verdict = run()
print("   " + verdict)
check("quarters complete", "0 quarterly files left to load" in out, out[-400:])
check("no filing fetched again (all seen)", STATE["filing_gets"] == fetched_first, STATE["filing_gets"] - fetched_first)
check("2021q1 trades saved", q("select count(*) from insider_trades where accession like 'Q1-%'") == [["20"]])

print("== third run: nothing new")
code, out, verdict = run()
print("   " + verdict)
check("up to date verdict", "everything is up to date" in verdict, verdict)

print("== a day later: the 6th is final, the 7th has a list")
CLOCK.when = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
DAYS[date(2026, 10, 7)] = list(FORM4)[2:4]
code, out, verdict = run()
print("   " + verdict)
check("cursor moved to the 6th", q("select value from collector_state where key='sec_daily_cursor'") == [["2026-10-06"]], out[-300:])

print("== a holiday: Monday 12 Oct has no list (EDGAR closed), the collector skips it without asking for it")
CLOCK.when = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)      # Wednesday: 13 Oct is final
for d, picks in ((date(2026, 10, 8), list(FORM4)[:1]), (date(2026, 10, 9), list(FORM4)[1:2]), (date(2026, 10, 13), list(FORM4)[2:3])):
    DAYS[d] = picks
STATE["daily_gets"] = []
code, out, verdict = run()
print("   " + verdict)
check("OK, not BLOCKED", code == 0 and verdict.startswith("VERDICT: OK"), verdict)
check("the holiday is named in the summary", "10/12: no list (EDGAR closed)" in out, out[-600:])
check("no request for the holiday's list", date(2026, 10, 12) not in STATE["daily_gets"], STATE["daily_gets"])
check("cursor moved past the holiday to the 13th", q("select value from collector_state where key='sec_daily_cursor'") == [["2026-10-13"]], out[-400:])

print("== a listed day that is refused is still a block")
CLOCK.when = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
REFUSED.add(date(2026, 10, 14))
code, out, verdict = run()
REFUSED.clear()
print("   " + verdict)
check("BLOCKED at step 3 on the refused day's list", code == 1 and "BLOCKED at step 3" in verdict and "form.20261014.idx" in verdict, verdict)

print("== tiny budget")
q("delete from collector_state where key like 'sec_%'")
q("drop table if exists insider_trades, insider_filings cascade")
code, out, verdict = run(max_requests=3)
print("   " + verdict)
check("ends OK with the budget used up", code == 0 and "budget is used up" in out, out[-500:])

print("== blocked")
STATE["block"] = True
code, out, verdict = run()
STATE["block"] = False
print("   " + verdict)
check("BLOCKED at step 2, exit 1", code == 1 and "BLOCKED at step 2" in verdict and "automated tools" in verdict, verdict)

print("== run records")
check("runs recorded as sec", int(q("select count(*) from collector_runs where collector='sec'")[0][0]) >= 5)
sys.exit(check.finish())
