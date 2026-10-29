"""
The Netherlands access check (python -m insider_tracking check netherlands) against a stand-in
of the AFM's register: an export that works, robots.txt saying no, a refusal.
"""
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from insider_tracking.checks.framework import PACING, CheckRunner
from insider_tracking.checks.netherlands import AfmEndpoints, NetherlandsCheck
from insider_tracking.runs.platform import Platform

from .support.checks import Checks
from .support.clocks import TestClock
from .support.commands import run as run_command
from .support.web import StandInHandler, serve

STATE = {"mode": "ok", "requests": []}
EXPORT = ("Datum meldingsplichtige transactie;Uitgevende instelling;Meldingsplichtige;Positie;Type transactie;"
          "Categorie;Instrument;Plaats transactie;Status;Unit\n"
          + "".join(f"2026-10-{d:02d};ACME NV;Person {d};CEO;Koop;Aandeel;ACME;Euronext;Actief;EUR\n" for d in range(1, 21)))


class Handler(StandInHandler):
    def do_GET(self):
        STATE["requests"].append(self.path)
        if STATE["mode"] == "blocked" and self.path != "/robots.txt":
            return self.reply(403, b"<html>Access Denied</html>", "text/html")
        if self.path == "/robots.txt":
            body = b"User-agent: *\nDisallow: /export.aspx\n" if STATE["mode"] == "robots_no" else b"User-agent: *\nCrawl-delay: 3\n"
            return self.reply(200, body, "text/plain")
        if self.path.startswith("/en/sector/registers/"):
            return self.reply(200, b"<html>1.234 results <a href='/export.aspx?type=x'>export</a></html>", "text/html")
        if self.path.startswith("/export.aspx"):
            return self.reply(200, EXPORT.encode("utf-8"), "text/csv")
        return self.reply(404, b"no", "text/plain")


BASE = serve(Handler)
OUTPUT = Path(tempfile.mkdtemp())
CHECK = NetherlandsCheck(AfmEndpoints(BASE), PACING.without_waiting())
PLATFORM = Platform(environ={"EFD_CONTACT_EMAIL": "tester@example.com"}, output_dir=OUTPUT,
                    clock=TestClock(datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)), sleep=lambda seconds: None)
check = Checks()
WANTED = {"ok": "VERDICT: OK - 20 rows", "robots_no": "VERDICT: BLOCKED at step 1", "blocked": "VERDICT: BLOCKED at step 2"}

for mode, wanted in WANTED.items():
    STATE["mode"], STATE["requests"] = mode, []
    result = run_command(CheckRunner(CHECK), [], PLATFORM)
    print(f"[{mode}] exit {result.code}: {result.last}  ({len(STATE['requests'])} requests)")
    check(f"{mode}: {wanted}", result.last.startswith(wanted), result.last)
    print("  files:", sorted(p.name for p in (OUTPUT / "netherlands").iterdir()))
sys.exit(check.finish())
