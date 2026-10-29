"""
The Swedish executives collector (python -m insider_tracking collect sweden) against a
stand-in of Finansinspektionen's register (serving real rows saved by the Sweden access check:
tests/fixtures/sweden) and of the ECB's exchange rates, with a real PostgreSQL test database:
the recent weeks, the history month by month, robots.txt, a refusal, the export's line limit.
"""
import csv
import io
import re
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from insider_tracking.runs.command import CollectorCommand
from insider_tracking.sources.ecb.site import EcbEndpoints
from insider_tracking.sources.sweden import site as sweden_site
from insider_tracking.sources.sweden.collector import SwedenSource

from .support.checks import Checks
from .support.clocks import TestClock
from .support.commands import run as run_command
from .support.database import TestDatabase
from .support.platforms import settings, test_platform
from .support.web import StandInHandler, serve

DB = TestDatabase("it_sweden").fresh()
SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sweden" / "recent_examples.csv"
with open(SAMPLE, encoding="utf-8", newline="") as file:
    SAMPLE_ROWS = list(csv.reader(file))
HEADER, DATA = SAMPLE_ROWS[0], SAMPLE_ROWS[1:]
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 25)


def export_for(start, end):
    """Rows of the real sample whose publication date falls in the window, re-dated for history months."""
    rows = []
    for r in DATA:
        pub = datetime.strptime(r[0], "%d/%m/%Y %H:%M:%S").date()
        if start <= pub <= end:
            rows.append(r)
    if not rows and end < date(2026, 10, 1):
        # history months: a handful of rows moved back in time
        for i, r in enumerate(DATA[:7]):
            r = list(r)
            day = start + timedelta(days=i % 20)
            r[0] = f"{day:%d/%m/%Y} 10:00:00"
            r[15] = f"{day:%d/%m/%Y} 00:00:00"
            rows.append(r)
    out = io.StringIO()
    out.write(";".join(HEADER) + ";\r\n")
    for r in rows:
        out.write(";".join(r) + ";\r\n")
    return ("﻿" + out.getvalue()).encode("utf-16-le")


def ecb_history():
    buf = io.BytesIO()
    lines = ["Date,USD,JPY,SEK,GBP,"]
    day = date(2016, 1, 1)
    while day <= STATE["history_until"]:
        if day.weekday() < 5:
            lines.append(f"{day:%Y-%m-%d},1.10,160.0,{10.5 + day.year - 2016:.4f},0.85,")
        day += timedelta(days=1)
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("eurofxref-hist.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def ecb_recent():
    days = []
    day = date(2026, 8, 1)
    while day <= date(2026, 10, 26):
        if day.weekday() < 5:
            days.append(f'<Cube time="{day:%Y-%m-%d}"><Cube currency="USD" rate="1.16"/><Cube currency="SEK" rate="11.13"/></Cube>')
        day += timedelta(days=1)
    return ('<?xml version="1.0"?><gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" '
            'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube>' + "".join(days) + "</Cube></gesmes:Envelope>").encode()


STATE = {"mode": "ok", "requests": [], "history_until": date(2026, 10, 23)}


class Handler(StandInHandler):
    def do_GET(self):
        STATE["requests"].append(self.path)
        if STATE["mode"] == "blocked" and "Publiceringsklient" in self.path:
            return self.reply(403, b"<html>Access Denied Reference&#32;&#35;18.abc</html>", "text/html")
        if self.path == "/robots.txt":
            if STATE["mode"] == "robots_no":
                return self.reply(200, b"User-agent: *\nDisallow: /\n", "text/plain")
            return self.reply(404, b"nope", "text/html")
        if self.path.startswith("/ecb/hist"):
            return self.reply(200, ecb_history(), "application/zip")
        if self.path.startswith("/ecb/recent"):
            return self.reply(200, ecb_recent(), "text/xml")
        m = re.search(r"Publiceringsdatum\.From=(\d{4}-\d{2}-\d{2})&Publiceringsdatum\.To=(\d{4}-\d{2}-\d{2})&button=export", self.path)
        if m:
            start, end = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
            body = export_for(start, end)
            if STATE["mode"] == "cap" and (end - start).days >= 40:
                # the real register stops its export at 1,000 lines: a long window comes back full
                lines = body.decode("utf-16").splitlines()
                lines = lines[:1] + (lines[1:] * 5)[:1000]
                body = ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-16-le")
            return self.reply(200, body, "application/vnd.ms-excel")
        return self.reply(404, b"not found", "text/html")


BASE = serve(Handler)
CLOCK = TestClock(NOW)
SOURCE = SwedenSource(sweden_site.FiEndpoints(BASE), EcbEndpoints(BASE + "/ecb/recent.xml", BASE + "/ecb/hist.zip"),
                      sweden_site.PACING.without_waiting())
PLATFORM = test_platform(DB, settings(), clock=CLOCK)
check = Checks()


def run(max_requests=20):
    result = run_command(CollectorCommand(SOURCE), ["--max-requests", str(max_requests)], PLATFORM)
    return result.code, result.out, result.last


def q(sql):
    return DB.query(sql)


print("== first run: rates history, robots, last 45 days, 12 history months")
STATE["requests"] = []
code, out, verdict = run()
print("   " + verdict)
print("\n".join("   " + l for l in out.splitlines() if l.startswith(("[", "Database", "Left out"))))
check("OK", code == 0 and verdict.startswith("VERDICT: OK"), verdict)
check("rates history loaded", "loaded the whole history" in out, out)
check("172 recent trades kept", "172 buys or sells of shares kept (172 new" in out, out[-900:])
check("12 history months loaded", "loaded 12 months, back to 2025-10-01" in out, out[-900:])
n = int(q("select count(*) from mar_trades")[0][0])
check("rows stored: 172 recent + 7 x 12 history", n == 172 + 84, n)
eur = q("select count(*) from mar_trades where value_eur is null")[0][0]
check("every row has a euro value", eur == "0", eur)
check("requests: robots + ecb + 1 recent + 12 history = 15", len(STATE["requests"]) == 15, len(STATE["requests"]))
sample = q("select person_name, issuer_short, kind, volume, price, currency, round(value_eur), status, position from mar_trades where person_name = 'Christian Hermelin' and trade_date >= '2026-10-01' order by trade_date")
print("   ", sample)
check("Hermelin's two Eastnine buys stored in SEK with euro value", len(sample) == 2 and sample[0][5] == "SEK" and sample[0][2] == "buy", sample)
revised = q("select count(*) from mar_trades where status = 'Revised'")[0][0]
check("revised rows kept with their status", revised == "4", revised)

print("== second run (two days later): nothing new; recent rates fetched; history continues")
STATE["requests"] = []
CLOCK.when = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
code, out, verdict = run()
print("   " + verdict)
check("recent rows seen before, not duplicated", "(0 new, 172 seen before)" in out and int(q("select count(*) from mar_trades")[0][0]) == 172 + 84 + 84, out[-700:])
check("rates: recent file fetched (stored rates ended 2026-10-23)", "new daily rates; newest rate 2026-10-26" in out, out[-900:])
check("history moved another 12 months", "back to 2024-10-01" in out, out[-900:])

print("== history completes eventually")
for _ in range(12):
    code, out, verdict = run()
    if "complete" in out:
        break
check("history complete back to 2016-07-03", "history is complete back to 2016-07-03" in out, out[-500:])
code, out, verdict = run()
check("then the history step says complete", "complete - the register is loaded back to 2016-07-03" in out, out[-500:])

print("== robots.txt says no")
STATE["mode"], STATE["requests"] = "robots_no", []
code, out, verdict = run()
print("   " + verdict)
check("BLOCKED at step 3, no export fetched", code == 1 and "BLOCKED at step 3" in verdict and not any("export" in p for p in STATE["requests"]), verdict)

print("== access denied")
STATE["mode"], STATE["requests"] = "blocked", []
code, out, verdict = run()
STATE["mode"] = "ok"
print("   " + verdict)
check("BLOCKED at step 4 with the reference", code == 1 and "BLOCKED at step 4" in verdict and "Reference #18.abc" in verdict, verdict)

print("== the export's 1,000-line limit: a full window is split in two")
q("drop table if exists mar_trades cascade")
q("delete from collector_state where key like 'se_%'")
STATE["mode"], STATE["requests"] = "cap", []
code, out, verdict = run()
STATE["mode"] = "ok"
print("   " + verdict)
check("the 45-day window was exported in halves", "203 lines published" in out and "172 buys or sells of shares kept" in out, out[-900:])
check("one extra request for the split (robots + 3 exports + 12 months)", len(STATE["requests"]) == 16, len(STATE["requests"]))

print("== run records")
check("runs recorded as sweden", int(q("select count(*) from collector_runs where collector='sweden'")[0][0]) >= 5)
sys.exit(check.finish())
