"""
The House collector (python -m insider_tracking collect house) against a stand-in House site
serving real reports (saved by the House access check: tests/fixtures/house) and a real
PostgreSQL test database: this year's list, the history year by year, a missing PDF, a block.
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from insider_tracking.db.congress import CongressReports
from insider_tracking.db.database import Database
from insider_tracking.db.schema import ensure_schema
from insider_tracking.runs.command import CollectorCommand
from insider_tracking.sources.house import site as house_site
from insider_tracking.sources.house.collector import HouseSource
from insider_tracking.sources.senate.parsing import parse_search_row

from .support.checks import Checks
from .support.clocks import TestClock
from .support.commands import run as run_command
from .support.database import TestDatabase
from .support.platforms import settings, test_platform
from .support.web import StandInHandler, serve

DB = TestDatabase("it_house").fresh()
SAMPLES = Path(__file__).resolve().parent / "fixtures" / "house"
PDFS = {p.stem: p.read_bytes() for p in (SAMPLES / "pdfs").glob("*.pdf")}

# ---- the lists: the real 2026 list cut down to the sampled reports, and small older lists
real = ElementTree.parse(SAMPLES / "2026FD-sample.xml").getroot()
keep = ([m for m in real.iter("Member") if (m.findtext("DocID") or "") in PDFS]
        + [m for m in real.iter("Member") if m.findtext("FilingType") == "C"][:10])


def xml_list(members):
    return ('<?xml version="1.0" encoding="utf-8"?><FinancialDisclosure>'
            + "".join(ElementTree.tostring(m, encoding="unicode") for m in members)
            + "</FinancialDisclosure>").encode()


def member(last, first, ftype, state, year, filed, doc):
    m = ElementTree.Element("Member")
    for tag, value in (("Prefix", "Hon."), ("Last", last), ("First", first), ("Suffix", ""),
                       ("FilingType", ftype), ("StateDst", state), ("Year", str(year)),
                       ("FilingDate", filed), ("DocID", doc)):
        ElementTree.SubElement(m, tag).text = value
    return m


LISTS = {
    2026: xml_list(keep),
    2025: xml_list([member("Doe", "John", "O", "OH01", 2025, "5/15/2025", "10070001")]),
    2020: xml_list([member("Chu", "Judy", "P", "CA27", 2020, "12/30/2020", "20017944"),
                    member("Scott", "Austin", "P", "GA08", 2020, "12/30/2020", "20017929")]),
    2016: xml_list([member("Gibbs", "Bob", "P", "OH07", 2016, "12/30/2016", "20006436"),
                    member("Maloney", "Carolyn B.", "P", "NY12", 2016, "12/29/2016", "20006403"),
                    member("Nobody", "Missing", "P", "TX01", 2016, "6/1/2016", "20005555")]),   # its PDF is missing
}
STATE = {"requests": 0, "list_gets": [], "pdf_gets": 0, "block": False}


class Handler(StandInHandler):
    def do_GET(self):
        STATE["requests"] += 1
        if STATE["block"]:
            return self.reply(403, b"<html>Access Denied Reference #18.dead</html>", "text/html")
        match = re.fullmatch(r"/public_disc/financial-pdfs/(\d{4})FD\.xml", self.path)
        if match:
            year = int(match.group(1))
            STATE["list_gets"].append(year)
            if year not in LISTS:
                return self.reply(404, b"not found", "text/html")
            etag = f'"list-{year}-{len(LISTS[year])}"'
            if self.headers.get("If-None-Match") == etag:
                return self.reply(304, b"", "text/xml", etag)
            return self.reply(200, LISTS[year], "text/xml", etag)
        match = re.fullmatch(r"/public_disc/ptr-pdfs/(\d{4})/(\d+)\.pdf", self.path)
        if match and match.group(2) in PDFS:
            STATE["pdf_gets"] += 1
            return self.reply(200, PDFS[match.group(2)], "application/pdf")
        return self.reply(404, b"not found", "text/html")

    def reply(self, code, body, ctype, etag=None):
        super().reply(code, body, ctype, {"ETag": etag} if etag else None)


BASE = serve(Handler)
SOURCE = HouseSource(house_site.HouseEndpoints(BASE), house_site.PACING.without_waiting())
PLATFORM = test_platform(DB, settings(), clock=TestClock(datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)))
check = Checks()


def run(max_requests=200):
    result = run_command(CollectorCommand(SOURCE), ["--max-requests", str(max_requests)], PLATFORM)
    return result.code, result.out, result.last


def q(sql):
    return DB.query(sql)


# the Senate's reports are already there: the House collector must leave them alone
SENATE = [["Jane", "Example", "Example, Jane (Senator)",
           f'<a href="/search/view/ptr/se-{n}/" target="_blank">Periodic Transaction Report for 10/0{n}/2026</a>',
           f"10/0{n}/2026"] for n in range(1, 4)]
db = Database(DB.connect())
ensure_schema(db)
CongressReports(db).add([parse_search_row(row, "https://efdsearch.senate.gov") for row in SENATE])
db.close()
senate_before = q("select count(*) from reports")[0][0]

print("== first run")
code, out, verdict = run()
print("   " + verdict)
print("\n".join("   " + l for l in out.splitlines() if l.startswith(("[", "Database", "History", "      could"))))
electronic_2026 = sum(1 for m in keep if m.findtext("FilingType") == "P" and len(m.findtext("DocID")) >= 8)
scanned_2026 = sum(1 for m in keep if m.findtext("FilingType") == "P" and len(m.findtext("DocID")) < 8)
check("OK", code == 0 and verdict.startswith("VERDICT: OK"), verdict)
rows = q("select chamber, kind, status, count(*) from reports where chamber = 'house' group by 1,2,3 order by 2,3")
print("   ", rows)
check("2026 reports stored, electronic read, scans not read",
      ["house", "electronic", "read", str(electronic_2026)] in rows and ["house", "paper", "not_read", str(scanned_2026)] in rows, rows)
check("the Senate's reports are untouched and marked senate",
      q("select count(*) from reports where chamber = 'senate'")[0][0] == senate_before)
check("history listed 3 years back (2024-2022), all missing", "read the lists for 2024, 2023, 2022" in out, out[-600:])
trades = q("select count(*) from transactions t join reports r using (report_path) where r.chamber = 'house'")[0][0]
check("transactions saved", int(trades) > 20, trades)
sample = q("select r.first_name, r.last_name, r.state_district, r.filer_type, t.ticker, t.trade_type, t.owner, t.amount_min, t.amount_max, t.transaction_date "
           "from transactions t join reports r using (report_path) where r.report_uuid = '20035551' order by t.row_number limit 2")
print("   ", sample)
check("a report's trades read correctly", sample[0][4] == "DDOG" and sample[0][5] == "purchase" and sample[0][6] == "Joint"
      and sample[0][7] == "50001" and sample[0][2] and sample[0][3] == "Representative", sample)
raw = q("select raw->>'filing_status', raw->>'subholding_of', raw->>'notification_date' from transactions where report_path = '/house/2026/20035551' and row_number = 1")
check("details kept in raw", raw == [["New", "Morgan Stanley Trust Account", "10/01/2026"]], raw)
check("run recorded as the House collector", q("select collector from collector_runs order by id desc limit 1") == [["house"]])

print("== second run: lists unchanged, history goes on")
STATE["list_gets"].clear()
code, out, verdict = run()
print("   " + verdict)
check("this year's list unchanged (304)", "2026: unchanged" in out and "2025: unchanged" in out, out[-500:])
check("2021-2019 listed, 2020's two reports found and read", "read the lists for 2021, 2020, 2019" in out and "2 new reports" in out, out[-500:])

print("== third run: 2016 has a report whose PDF is missing")
code, out, verdict = run()
print("   " + verdict)
check("missing PDF marked failed, run still OK", code == 0 and q("select status, note from reports where report_uuid = '20005555'") == [["failed", "file not found (HTTP 404)"]],
      q("select status, note from reports where report_uuid = '20005555'"))

print("== fourth and fifth runs: history complete, then up to date")
code, out, verdict = run()
print("   " + verdict)
check("history complete", "complete - every list back to 2013 is read" in out, out[-400:])
code, out, verdict = run()
print("   " + verdict)
check("everything up to date", "no new reports, everything is up to date" in verdict, verdict)

print("== blocked")
STATE["block"] = True
q("delete from collector_state where key like 'house_list_etag%'")
code, out, verdict = run()
STATE["block"] = False
print("   " + verdict)
check("BLOCKED at step 2, exit 1", code == 1 and "BLOCKED at step 2" in verdict, verdict)

print("== Senate collector's view stays Senate-only")
senate_counts = CongressReports(Database(DB.connect())).counts("senate")
check("counts for the Senate exclude House reports", senate_counts["reports"] == int(senate_before), senate_counts)

check("the stand-in Senate reports were there all along", int(senate_before) == len(SENATE), senate_before)
sys.exit(check.finish())
