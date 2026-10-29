"""
The SEC access check (python -m insider_tracking check sec) against a stand-in SEC site: the
quarterly data file, the daily list and a Form 4, a changed page, the SEC's refusal page.
"""
import io
import json
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from insider_tracking.checks.framework import PACING, CheckRunner
from insider_tracking.checks.sec import SecCheck
from insider_tracking.runs.platform import Platform
from insider_tracking.sources.sec.site import SecEndpoints

from .sites.sec_files import NDT, OWN, SUB, ndt, own, sub, tsv
from .support.checks import Checks
from .support.clocks import TestClock
from .support.commands import run as run_command
from .support.web import StandInHandler, serve

FILES = {}          # path -> (status, content type, bytes)


class Handler(StandInHandler):
    def do_GET(self):
        status, ctype, body = FILES.get(self.path.split("?")[0], (404, "text/html", b"<h1>Not Found</h1>"))
        self.reply(status, body, ctype)


zbuf = io.BytesIO()
with zipfile.ZipFile(zbuf, "w") as z:
    z.writestr("SUBMISSION.tsv", tsv(SUB, [sub("0001-26-1", "4", "320193", "Apple Inc.", "AAPL"),
                                           sub("0001-26-2", "4", "789019", "MICROSOFT CORP", "MSFT"),
                                           sub("0001-26-3", "3", "789019", "MICROSOFT CORP", "MSFT")]))
    z.writestr("REPORTINGOWNER.tsv", tsv(OWN, [own("0001-26-1", "111", "Cook Timothy D", "Officer", "CEO"),
                                               own("0001-26-2", "222", "Gates William H", "Director", ""),
                                               own("0001-26-3", "333", "New Director", "Director", "")]))
    z.writestr("NONDERIV_TRANS.tsv", tsv(NDT, [ndt("0001-26-1", "1", "S", "50000", "210.5", "D"),
                                               ndt("0001-26-1", "2", "F", "1000", "210", "D"),
                                               ndt("0001-26-2", "3", "P", "1000", "420", "A"),
                                               ndt("0001-26-2", "4", "A", "500", "0", "A")]))
    z.writestr("FORM345_metadata.json", "{}")
FILES["/files/structureddata/data/insider-transactions-data-sets/2026q3_form345.zip"] = (200, "application/zip", zbuf.getvalue())
FILES["/dera/data/form-345"] = (200, "text/html; charset=utf-8", (
    '<html><body><a href="/files/structureddata/data/insider-transactions-data-sets/2026q3_form345.zip">2026 Q3</a>'
    '<a href="/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q2_form345.zip">2026 Q2</a>'
    "</body></html>").encode())
FILES["/Archives/edgar/daily-index/2026/QTR4/index.json"] = (200, "application/json", json.dumps(
    {"directory": {"item": [{"name": "form.20261021.idx", "type": "file"},
                            {"name": "form.20261022.idx", "type": "file"},
                            {"name": "master.20261022.idx", "type": "file"}]}}).encode())
IDX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    October 22, 2026

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
10-Q        SOME COMPANY INC                                              1000001     20261022    edgar/data/1000001/0001000001-26-000001.txt
4           Apple Inc.                                                    320193      20261022    edgar/data/320193/0000320193-26-000101.txt
4           Cook Timothy D                                                1214156     20261022    edgar/data/1214156/0000320193-26-000101.txt
4/A         MICROSOFT CORP                                                789019      20261022    edgar/data/789019/0000789019-26-000050.txt
SC 13G      BIG FUND LP                                                   2000002     20261022    edgar/data/2000002/0002000002-26-000003.txt
"""
FILES["/Archives/edgar/daily-index/2026/QTR4/form.20261022.idx"] = (200, "text/plain", IDX.encode())
FORM4 = """<SEC-DOCUMENT>0000320193-26-000101.txt : 20261022
<TYPE>4
<TEXT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0508</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2026-10-20</periodOfReport>
    <aff10b5One>1</aff10b5One>
    <issuer><issuerCik>0000320193</issuerCik><issuerName>Apple Inc.</issuerName><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>
    <reportingOwner>
        <reportingOwnerId><rptOwnerCik>0001214156</rptOwnerCik><rptOwnerName>Cook Timothy D</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle><value>Common Stock</value></securityTitle>
            <transactionDate><value>2026-10-20</value></transactionDate>
            <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>S</transactionCode><equitySwapInvolved>0</equitySwapInvolved></transactionCoding>
            <transactionAmounts><transactionShares><value>129963</value></transactionShares><transactionPricePerShare><value>255.12</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
            <postTransactionAmounts><sharesOwnedFollowingTransaction><value>3280295</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
            <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>
</XML>
</TEXT>
</SEC-DOCUMENT>
"""
FILES["/Archives/edgar/data/320193/0000320193-26-000101.txt"] = (200, "text/plain", FORM4.encode())

BASE = serve(Handler)
OUT = Path(tempfile.mkdtemp())
CHECK = SecCheck(SecEndpoints(BASE), PACING.without_waiting())
PLATFORM = Platform(environ={}, output_dir=OUT, clock=TestClock(datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)),
                    sleep=lambda seconds: None)
check = Checks()


def run(args=("--email", "tester@example.com")):
    result = run_command(CheckRunner(CHECK), args, PLATFORM)
    return result.code, result.out


check.section("everything works")
code, out = run()
print(out)
summary = json.loads((OUT / "sec" / "summary.json").read_text())
quarter = json.loads((OUT / "sec" / "quarter_summary.json").read_text())
examples = json.loads((OUT / "sec" / "form4_examples.json").read_text())
print("quarter:", {k: quarter[k] for k in ("submissions", "buys", "sells", "buys_sells_value_usd", "storage_estimate_mb_per_year")})
print("form 4:", [(e["ticker"], e["insiders"][0]["name"], [(t["code"], t["shares"], t["price"]) for t in e["trades"]])
                  for e in examples])
check("verdict OK", code == 0 and summary["verdict"] == "OK", summary["verdict"])
check("the quarter's counts: 1 buy, 1 sell, with their values",
      quarter["buys"] == 1 and quarter["sells"] == 1 and quarter["buys_sells_value_usd"] == {"buys": 420000, "sells": 10525000},
      quarter)
check("the Form 4 read once (listed twice), the 4/A skipped",
      len(examples) == 1 and examples[0]["trades"][0]["code"] == "S" and examples[0]["plan_10b5_1"] == "1", examples)
check("the quarterly ZIP is not kept", not list((OUT / "sec").glob("*.zip")))

check.section("a changed page, a refusal, no email")
FILES["/dera/data/form-345"] = (200, "text/html", b"<html>no links here</html>")
code, out = run()
check("a page without ZIP links is CHANGED at step 1", "VERDICT: CHANGED at step 1" in out, out[-300:])
FILES["/dera/data/form-345"] = (403, "text/html", b"<html><h1>Your Request Originates from an Undeclared Automated Tool</h1></html>")
code, out = run()
check("the SEC's automated-tool page is BLOCKED", "VERDICT: BLOCKED at step 1" in out, out[-300:])
code, out = run(args=())
check("no email: NOT_RUN, exit 1", "VERDICT: NOT_RUN" in out and code == 1, out[-300:])
sys.exit(check.finish())
