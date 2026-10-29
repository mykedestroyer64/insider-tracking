"""
The House access check (python -m insider_tracking check house) against a stand-in House site:
this year's list, electronic and scanned reports, last year's list in January, a refusal.
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from insider_tracking.checks.framework import PACING, CheckRunner
from insider_tracking.checks.house import HouseCheck
from insider_tracking.runs.platform import Platform
from insider_tracking.sources.house.site import HouseEndpoints

from .support.checks import Checks
from .support.clocks import TestClock
from .support.commands import run as run_command
from .support.web import StandInHandler, serve

PDFS = Path(__file__).resolve().parent / "fixtures" / "house_check"
FILES = {}          # path -> (status, content type, bytes)


class Handler(StandInHandler):
    def do_GET(self):
        status, ctype, body = FILES.get(self.path.split("?")[0], (404, "text/html", b"<h1>Not Found</h1>"))
        self.reply(status, body, ctype)


def member(last, first, ftype, state, year, filed, doc):
    return (f"<Member><Prefix>Hon.</Prefix><Last>{last}</Last><First>{first}</First><Suffix />"
            f"<FilingType>{ftype}</FilingType><StateDst>{state}</StateDst><Year>{year}</Year>"
            f"<FilingDate>{filed}</FilingDate><DocID>{doc}</DocID></Member>")


def house_list(members):
    return (f'<?xml version="1.0" encoding="utf-8"?><FinancialDisclosure>{"".join(members)}'
            "</FinancialDisclosure>").encode()


# report.pdf has two trades (Apple bought by a spouse, Microsoft partly sold jointly);
# report_one_trade.pdf only the first; scanned.pdf is a picture without text
REPORT = (PDFS / "report.pdf").read_bytes()
members_2026 = [member("Pelosi", "Nancy", "P", "CA11", 2026, "10/20/2026", "20033001"),
                member("Pelosi", "Nancy", "P", "CA11", 2026, "10/01/2026", "20032990"),
                member("Crenshaw", "Daniel", "P", "TX02", 2026, "10/19/2026", "20033000"),
                member("Smith", "Jane", "P", "NY10", 2026, "10/18/2026", "8221001"),
                member("Doe", "John", "O", "OH01", 2026, "5/15/2026", "10077001"),
                member("Roe", "Ann", "A", "FL05", 2026, "6/1/2026", "10077002")]
FILES["/public_disc/financial-pdfs/2026FD.xml"] = (200, "text/xml", house_list(members_2026))
FILES["/public_disc/financial-pdfs/2020FD.xml"] = (200, "text/xml", house_list([
    member("Old", "Timer", "P", "CA01", 2020, "3/2/2020", "20016923")]))
for doc in ("20033001", "20032990", "20033000"):
    FILES[f"/public_disc/ptr-pdfs/2026/{doc}.pdf"] = (200, "application/pdf", REPORT)
FILES["/public_disc/ptr-pdfs/2026/8221001.pdf"] = (200, "application/pdf", (PDFS / "scanned.pdf").read_bytes())
FILES["/public_disc/ptr-pdfs/2020/20016923.pdf"] = (200, "application/pdf", (PDFS / "report_one_trade.pdf").read_bytes())
# 2016FD.xml is missing on purpose: the step should note it and go on

BASE = serve(Handler)
OUT = Path(tempfile.mkdtemp())
CHECK = HouseCheck(HouseEndpoints(BASE), PACING.without_waiting())
PLATFORM = Platform(environ={}, output_dir=OUT, clock=TestClock(datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)),
                    sleep=lambda seconds: None)
check = Checks()


def run(args=("--email", "tester@example.com")):
    result = run_command(CheckRunner(CHECK), args, PLATFORM)
    return result.code, result.out


check.section("everything works")
code, out = run()
print(out)
summary = json.loads((OUT / "house" / "summary.json").read_text())
check("verdict OK", code == 0 and summary["verdict"] == "OK", summary["verdict"])
reports = json.loads((OUT / "house" / "reports.json").read_text())
kinds = [r["kind"] for r in reports]
print("house reports:", [(r["doc_id"], r["kind"], r.get("date_pairs"), r.get("tickers")) for r in reports])
check("2 new electronic reports (Pelosi once, the newest only, and Crenshaw)", kinds.count("electronic") == 2, kinds)
check("the scanned report and the 2020 report are there",
      "scanned" in kinds and any(k.startswith("electronic, 2020") for k in kinds), kinds)
scanned = [r for r in reports if r["kind"] == "scanned"]
check("the scanned PDF has no text", scanned and scanned[0]["text_chars"] == 0, scanned)
check("only this year's list is saved",
      (OUT / "house" / "2026FD.xml").exists() and not (OUT / "house" / "2020FD.xml").exists())
check("the email is not in the output",
      "tester@example.com" not in "".join(p.read_text(errors="ignore") for p in (OUT / "house").rglob("*.json")))

check.section("refused, and January (only last year's list exists yet)")
FILES["/public_disc/financial-pdfs/2026FD.xml"] = (403, "text/html", b"<html>Access Denied Reference #18.abc</html>")
code, out = run()
check("a 403 is BLOCKED at step 1", "VERDICT: BLOCKED at step 1" in out, out[-300:])
FILES["/public_disc/financial-pdfs/2026FD.xml"] = (404, "text/html", b"not found")
FILES["/public_disc/financial-pdfs/2025FD.xml"] = (200, "text/xml", house_list(members_2026[:1]))
code, out = run()
check("falls back to the 2025 list in January", "in the 2025 list" in out, out[-400:])
sys.exit(check.finish())
