"""
The Sweden access check (python -m insider_tracking check sweden) against a stand-in of
Finansinspektionen's register: robots.txt and its crawl delay, the search page, two exports,
robots.txt saying no, a refusal.
"""
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from insider_tracking.checks.framework import PACING, CheckRunner
from insider_tracking.checks.sweden import SwedenCheck
from insider_tracking.runs.platform import Platform
from insider_tracking.sources.sweden.site import FiEndpoints

from .support.checks import Checks
from .support.clocks import TestClock
from .support.commands import run as run_command
from .support.web import StandInHandler, serve

HEADER = ("Publication date;Issuer;LEI-code;Notifier;Person discharging managerial responsibilities;Position;"
          "Closely associated;Amendment;Details of amendment;Initial notification;Linked to share option programme;"
          "Nature of transaction;Intrument type;Instrument name;ISIN;Transaction date;Volume;Unit;Price;Currency;"
          "Trading venue;Status;")
ROW = ("23/10/2026 23:26:02;Bonava AB (publ);213800SLYJ6HYXWP8L95;Nils Javier Styf;Nils Javier Styf;"
       "Member of the Board of Directors;No;No;;No;No;Acquisition;Share;Bonava B;SE0020050417;22/10/2026;"
       "{vol};Quantity;{price};SEK;NASDAQ STOCKHOLM AB;Current;")
STATE = {"mode": "ok", "requests": []}


def export(rows):
    lines = [HEADER] + [ROW.format(vol=1000 + i, price=f"{10 + i / 10:.2f}") for i in range(rows)]
    return ("﻿" + "\r\n".join(lines) + "\r\n").encode("utf-16-le")


class Handler(StandInHandler):
    def do_GET(self):
        STATE["requests"].append(self.path)
        if STATE["mode"] == "blocked" and "robots" not in self.path:
            return self.reply(403, b"<html>Access Denied Reference&#32;&#35;18.abc</html>", "text/html")
        if self.path == "/robots.txt":
            if STATE["mode"] == "robots_no":
                return self.reply(200, b"User-agent: *\nDisallow: /\n", "text/plain")
            return self.reply(200, b"User-agent: *\nDisallow: /secret/\nCrawl-delay: 2\n", "text/plain")
        if self.path.startswith("/Publiceringsklient/en-GB/Search/Start/Insyn"):
            return self.reply(200, b"<html>Person discharging managerial responsibilities ... Showing 1 to 10 of 169166 result</html>", "text/html")
        m = re.search(r"Publiceringsdatum\.From=(\d{4})-", self.path)
        if m and "button=export" in self.path:
            rows = 60 if m.group(1) == "2026" else 7
            return self.reply(200, export(rows), "application/vnd.ms-excel")
        return self.reply(404, b"not found", "text/html")


BASE = serve(Handler)
OUTPUT = Path(tempfile.mkdtemp())
CHECK = SwedenCheck(FiEndpoints(BASE), PACING.without_waiting())
PLATFORM = Platform(environ={"EFD_CONTACT_EMAIL": "tester@example.com"}, output_dir=OUTPUT,
                    clock=TestClock(datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)), sleep=lambda seconds: None)
check = Checks()


def run():
    result = run_command(CheckRunner(CHECK), [], PLATFORM)
    return result.code, result.out


code, out = run()
print(out.strip().splitlines()[-1])
check("OK verdict", code == 0 and "VERDICT: OK - 60 notifications in the last 7 days, 7 in July 2016" in out, out[-600:])
check("robots delay noted", "asks for 2 s" in out, out)
check("register total read", "169,166 notifications" in out, out)
check("examples saved", (OUTPUT / "sweden" / "recent_examples.csv").exists())
check("four requests sent", len(STATE["requests"]) == 4, STATE["requests"])

STATE["mode"], STATE["requests"] = "robots_no", []
code, out = run()
print(out.strip().splitlines()[-1])
check("robots disallow -> BLOCKED at step 1, nothing else fetched", code == 1 and "BLOCKED at step 1" in out and len(STATE["requests"]) == 1, out[-400:])

STATE["mode"], STATE["requests"] = "blocked", []
code, out = run()
print(out.strip().splitlines()[-1])
check("403 -> BLOCKED at step 2", code == 1 and "BLOCKED at step 2" in out and "Access Denied" in out, out[-400:])
sys.exit(check.finish())
