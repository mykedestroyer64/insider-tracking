"""
house.py - can a script read the House Clerk's financial disclosure site, and what do its
trade reports look like? It saves real examples, so the House collector can be built from them:

  1. download this year's list of filings (one XML file, one entry per report)
  2. pick the Periodic Transaction Reports (filing type "P"), newest first
  3. download the newest electronic reports (PDF files with text in them)
  4. download a few scanned paper reports (PDF files with only pictures)
  5. download a few older electronic reports, since the layout changed over the years
  6. read the text of each PDF and look for transactions in it

Run it with your own email:   python -m insider_tracking check house --email you@example.com
Reading the PDFs needs the pdfplumber library. Results are saved in "output/house".
"""

import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

from ..core.errors import SiteStop
from ..core.status import CHANGED, OK, SKIPPED
from ..web.checks import BlockPageCheck, unexpected
from ..web.describe import http_codes
from ..sources.house.site import HouseEndpoints
from .framework import PACING, Check, CheckDefinition, CheckRunner, common_summary, script_fields, shown, times, write_json

NEW_ELECTRONIC = 12          # newest electronic reports to download (one per member)
PAPER = 3                    # newest scanned reports to download
OLDER_YEARS = (2020, 2016)   # older lists to take electronic reports from
PER_OLDER_YEAR = 2

# What a transaction looks like in a report's text
DATE_PAIR = re.compile(r"\b(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\b")    # trade date, notification date
AMOUNT = re.compile(r"\$[\d,]+(?:\s*-\s*\$[\d,]+)?|Over \$[\d,]+|Spouse/DC Over \$[\d,]+")
TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,7})\)")
ASSET_CODE = re.compile(r"\[([A-Z]{2})\]")

DEFINITION = CheckDefinition(
    title="House access check",
    description="Check whether this computer can read the House Clerk's financial disclosure site.",
    steps=("Download this year's list of filings", "Pick the trade reports", "Download new electronic reports",
           "Download scanned reports", "Download older electronic reports", "Read the reports' text"),
    site="the House site",
    folder="house",
    problem_page_limit=None,
    advice={
        "BLOCKED": ["The House site refused the script. Try one more run later; if it is blocked",
                    "again, stop there and share this result."],
        "SITE_DOWN": ["The House site had a server error or showed a maintenance page. Try again later."],
        "CHANGED": ["A file did not look the way the script expects, so the House site may work",
                    "differently now. Keep the output folder and share it."],
    },
)


def parse_filing_date(text):
    """The list writes dates as M/D/YYYY (no leading zeros)."""
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", (text or "").strip())
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def is_electronic(doc_id):
    """Electronic (e-filed) reports have 8-digit document IDs; scans have shorter ones."""
    return doc_id.isdigit() and len(doc_id) >= 8


def newest_first(filings):
    return sorted(filings, key=lambda f: (parse_filing_date(f["filing_date"]) or date.min, f["doc_id"]),
                  reverse=True)


def one_per_member(filings, count):
    """The newest reports, at most one per member, so the examples cover different people."""
    picked, seen = [], set()
    for filing in newest_first(filings):
        who = (filing["last"].lower(), filing["first"].lower(), filing["state_district"])
        if who in seen:
            continue
        seen.add(who)
        picked.append(filing)
        if len(picked) == count:
            break
    return picked


def read_pdf_text(path):
    """All the text in a PDF, page by page (empty for scanned pages)."""
    import pdfplumber   # imported here so the rest of the check works without it
    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def look_for_transactions(text):
    """Rough count of what a transactions table holds, to judge how readable the text is."""
    return {
        "date_pairs": len(DATE_PAIR.findall(text)),     # one per transaction: trade date + notification date
        "amounts": len(AMOUNT.findall(text)),
        "tickers": sorted(set(TICKER.findall(text))),
        "asset_codes": sorted(set(ASSET_CODE.findall(text))),
    }


class HouseCheck(Check):
    def __init__(self, endpoints=HouseEndpoints(), pacing=PACING):
        self.endpoints = endpoints
        self.definition = replace(DEFINITION, pacing=pacing)
        self.check = BlockPageCheck(name_the_page=False)

    def clear(self, folder):
        (folder / "pdfs").mkdir(parents=True, exist_ok=True)
        for old in list(folder.glob("*.json")) + list(folder.glob("*.xml")) + list((folder / "pdfs").glob("*")):
            old.unlink()   # results of an earlier run

    def setup(self, run):
        run.info.update(counts={"filings_this_year": 0, "trade_reports": 0, "electronic": 0, "paper": 0,
                                "downloaded": 0, "with_text": 0, "transactions_seen": 0},
                        reports=[], list_year=None)

    # ---------------------------------------------------------------- helpers

    def download_list(self, run, client, year, save=True):
        """One year's list of filings: (filings, codes), or (None, codes) if there is none yet."""
        response = client.get(self.endpoints.yearly_list.format(year=year), check=self.check, ok_codes=(404,))
        if response.status_code == 404:
            return None, http_codes(response)
        if save:
            (run.folder / f"{year}FD.xml").write_bytes(response.content)
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as error:
            raise unexpected(response, f"the {year} list is not valid XML ({error})") from None
        filings = []
        for member in root.iter("Member"):
            def field(tag):
                return (member.findtext(tag) or "").strip()
            filings.append({
                "prefix": field("Prefix"), "last": field("Last"), "first": field("First"),
                "suffix": field("Suffix"), "filing_type": field("FilingType"),
                "state_district": field("StateDst"), "year": field("Year") or str(year),
                "filing_date": field("FilingDate"), "doc_id": field("DocID"),
            })
        if not filings:
            raise unexpected(response, f"the {year} list has no <Member> entries")
        if not all(f["doc_id"] and f["filing_type"] for f in filings):
            raise unexpected(response, f"some entries in the {year} list have no DocID or FilingType")
        return filings, http_codes(response)

    def download_report(self, run, client, filing):
        """One report's PDF, saved. Returns the saved path."""
        url = self.endpoints.report.format(year=filing["year"], doc_id=filing["doc_id"])
        response = client.get(url, check=self.check)
        if not response.content.startswith(b"%PDF"):
            raise unexpected(response, f"report {filing['doc_id']} is not a PDF file")
        path = run.folder / "pdfs" / f"{filing['doc_id']}.pdf"
        path.write_bytes(response.content)
        filing["url"] = url
        filing["pdf_bytes"] = len(response.content)
        return path

    # ---------------------------------------------------------------- steps

    def run_steps(self, run, client):
        log, counts, reports = run.log, run.info["counts"], run.info["reports"]

        log.begin(1)
        this_year = run.today.year
        filings, codes = self.download_list(run, client, this_year)
        run.info["list_year"] = this_year
        if filings is None:   # early January: this year's list may not exist yet
            filings, more = self.download_list(run, client, this_year - 1)
            codes += more
            run.info["list_year"] = this_year - 1
            if filings is None:
                raise SiteStop(CHANGED, f"no list of filings for {this_year} or {this_year - 1} (HTTP 404)")
        types = {}
        for filing in filings:
            types[filing["filing_type"]] = types.get(filing["filing_type"], 0) + 1
        counts["filings_this_year"] = len(filings)
        log.end(OK, f"{len(filings)} filings in the {run.info['list_year']} list, by type: "
                    + ", ".join(f"{t} {n}" for t, n in sorted(types.items(), key=lambda x: -x[1])), codes)

        log.begin(2)
        ptrs = [f for f in filings if f["filing_type"] == "P"]
        electronic = [f for f in ptrs if is_electronic(f["doc_id"])]
        paper = [f for f in ptrs if not is_electronic(f["doc_id"])]
        if not ptrs:
            raise SiteStop(CHANGED, "no filings of type P (Periodic Transaction Report) in the list")
        counts.update(trade_reports=len(ptrs), electronic=len(electronic), paper=len(paper))
        dates = [d for d in (parse_filing_date(f["filing_date"]) for f in ptrs) if d]
        newest = max(dates) if dates else None
        log.end(OK, f"{len(ptrs)} trade reports: {len(electronic)} electronic, {len(paper)} scanned"
                    + (f"; newest filed {newest:%m/%d/%Y}" if newest else ""))
        write_json(run.folder / "trade_reports_list.json", newest_first(ptrs))

        for number, candidates, count, kind in ((3, electronic, NEW_ELECTRONIC, "electronic"),
                                                (4, paper, PAPER, "scanned")):
            log.begin(number)
            picked = one_per_member(candidates, count)
            for filing in picked:
                filing["path"] = self.download_report(run, client, filing)
                filing["kind"] = kind
                reports.append(filing)
            log.end(OK if picked else SKIPPED, f"{len(picked)} downloaded" if picked else "none in the list")

        log.begin(5)
        older_count, notes = 0, []
        for year in OLDER_YEARS:
            old_filings, _ = self.download_list(run, client, year, save=False)
            if old_filings is None:
                notes.append(f"no {year} list")
                continue
            old_electronic = [f for f in old_filings if f["filing_type"] == "P" and is_electronic(f["doc_id"])]
            for filing in one_per_member(old_electronic, PER_OLDER_YEAR):
                filing["path"] = self.download_report(run, client, filing)
                filing["kind"] = f"electronic, {year}"
                reports.append(filing)
                older_count += 1
        log.end(OK, f"{older_count} downloaded from {', '.join(str(y) for y in OLDER_YEARS)}"
                    + (f" ({'; '.join(notes)})" if notes else ""))
        counts["downloaded"] = len(reports)

        log.begin(6)
        try:
            import pdfplumber  # noqa: F401
        except ImportError:
            log.end(SKIPPED, "pdfplumber is not installed (pip install pdfplumber)")
            return
        for filing in reports:
            pages = read_pdf_text(filing["path"])
            text = "\n".join(pages)
            (run.folder / "pdfs" / f"{filing['doc_id']}.txt").write_text(text, encoding="utf-8")
            found = look_for_transactions(text)
            filing.update(pages=len(pages), text_chars=len(text.strip()), **found)
            if text.strip():
                counts["with_text"] += 1
            counts["transactions_seen"] += found["date_pairs"]
        scanned_with_text = sum(1 for f in reports if f["kind"] == "scanned" and f.get("text_chars"))
        electronic_without = sum(1 for f in reports if f["kind"] != "scanned" and not f.get("text_chars"))
        detail = (f"{counts['with_text']} of {len(reports)} PDFs have text; about "
                  f"{counts['transactions_seen']} transactions seen")
        if electronic_without:
            detail += f"; {electronic_without} electronic report(s) without text"
        if scanned_with_text:
            detail += f"; {scanned_with_text} scanned report(s) with text"
        if reports and not counts["with_text"]:
            raise SiteStop(CHANGED, "none of the downloaded PDFs has any text")
        log.end(OK, detail)

    # ------------------------------------------------------------ summing up

    def outcome(self, run):
        counts = run.info["counts"]
        return (f"- {counts['trade_reports']} trade reports in the {run.info['list_year']} list "
                f"({counts['electronic']} electronic, {counts['paper']} scanned); "
                f"{counts['downloaded']} downloaded, {counts['with_text']} with text, "
                f"about {counts['transactions_seen']} transactions seen")

    def finish(self, run):
        for filing in run.info["reports"]:
            filing["path"] = str(Path(filing["path"]).relative_to(run.folder))
        write_json(run.folder / "reports.json", run.info["reports"])

    def summary(self, run, verdict_line):
        return {**common_summary(run, verdict_line), **times(run), "list_year": run.info["list_year"],
                "steps": run.log.steps, "counts": run.info["counts"], **script_fields()}

    def saved_line(self, run):
        return (f"Saved in {shown(run.folder)}: this year's list, {len(run.info['reports'])} PDFs with their text, "
                "reports.json, summary.json")


def command():
    """`python -m insider_tracking check house`."""
    return CheckRunner(HouseCheck())
