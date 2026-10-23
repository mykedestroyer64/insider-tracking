"""
house_check.py - access test for the US House of Representatives' trade reports.

Checks whether a script can read the House Clerk's financial disclosure site
(https://disclosures-clerk.house.gov) and saves real examples of its trade
reports, so the House collector can be built from them:

  1. download this year's list of filings (one XML file, one entry per report)
  2. pick the Periodic Transaction Reports (filing type "P"), newest first
  3. download the newest electronic reports (PDF files with text in them)
  4. download a few scanned paper reports (PDF files with only pictures)
  5. download a few older electronic reports, since the layout changed over the years
  6. read the text of each PDF and look for transactions in it

Each step gets one status, as in efd_check.py:
  OK         it worked
  BLOCKED    the site refused us (HTTP 403/429 or an "Access Denied" page)
  SITE_DOWN  the site had a server error or showed a maintenance page
  CHANGED    a file did not look the way this script expects
Steps that never ran because an earlier step failed are marked NOT_RUN.

It is polite in the same way: 2-3 seconds between requests, a time limit on
every request, at most one retry, and a User-Agent that names the project and
gives a contact email.

Run it (with your own email):
    python house_check.py --email you@example.com
On GitHub Actions the email comes from the EFD_CONTACT_EMAIL secret instead.
Reading the PDFs needs the pdfplumber library:  pip install pdfplumber

Results are saved in the "output/house" folder. The last line printed is the
verdict. Exit code: 0 if the verdict is OK, 1 otherwise.
"""

import argparse
import json
import os
import re
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import efd_check   # the polite web session and the status checks are shared with the Senate test
from efd_check import CHANGED, NOT_RUN, OK, SKIPPED, StepFailed, check_response, http_codes, short_error


# ------------------------------------------------------------------ settings

PROJECT = "insider-tracking"
VERSION = "0.1"

BASE_URL = "https://disclosures-clerk.house.gov"
INDEX_PATH = "/public_disc/financial-pdfs/{year}FD.xml"      # the list of every filing in a year
PTR_PATH = "/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"      # one trade report

NEW_ELECTRONIC = 12          # newest electronic reports to download (one per member)
PAPER = 3                    # newest scanned reports to download
OLDER_YEARS = (2020, 2016)   # older lists to take electronic reports from
PER_OLDER_YEAR = 2

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "house"

STEP_NAMES = {
    1: "Download this year's list of filings",
    2: "Pick the trade reports",
    3: "Download new electronic reports",
    4: "Download scanned reports",
    5: "Download older electronic reports",
    6: "Read the reports' text",
}
TOTAL_STEPS = len(STEP_NAMES)

# What a transaction looks like in a report's text
DATE_PAIR = re.compile(r"\b(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\b")    # trade date, notification date
AMOUNT = re.compile(r"\$[\d,]+(?:\s*-\s*\$[\d,]+)?|Over \$[\d,]+|Spouse/DC Over \$[\d,]+")
TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,7})\)")
ASSET_CODE = re.compile(r"\[([A-Z]{2})\]")


# ------------------------------------------------------------------- helpers

class StepLog:
    """Prints each step's result and keeps it for summary.json."""

    def __init__(self):
        self.steps = []
        self.current = 0

    def begin(self, number):
        self.current = number
        label = f"{STEP_NAMES[number]} ".ljust(40, ".")
        print(f"[{number}/{TOTAL_STEPS}] {label}", end=" ", flush=True)

    def end(self, status, detail="", codes=()):
        codes = list(codes)
        http = f" (HTTP {' -> '.join(str(code) for code in codes)})" if codes else ""
        print(f"{status}{http}" + (f" - {detail}" if detail else ""), flush=True)
        self.steps.append({"step": self.current, "name": STEP_NAMES[self.current],
                           "status": status, "http_codes": codes, "detail": detail})

    def mark_rest(self, status, detail=""):
        for number in range(self.current + 1, TOTAL_STEPS + 1):
            self.begin(number)
            self.end(status, detail)


def shown(path):
    """A path relative to this script's folder when possible (shorter to read)."""
    try:
        return str(path.relative_to(Path(__file__).resolve().parent))
    except ValueError:
        return str(path)


def save_json(filename, data):
    with open(OUTPUT_DIR / filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


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


def download_list(web, year, save=True):
    """GET one year's list of filings. Returns (filings, codes), or (None, codes) if there is none yet."""
    response = web.request("GET", BASE_URL + INDEX_PATH.format(year=year))
    if response.status_code == 404:
        return None, http_codes(response)
    check_response(response)
    if save:
        (OUTPUT_DIR / f"{year}FD.xml").write_bytes(response.content)
    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError as error:
        raise efd_check.unexpected(response, f"the {year} list is not valid XML ({error})") from None
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
        raise efd_check.unexpected(response, f"the {year} list has no <Member> entries")
    if not all(f["doc_id"] and f["filing_type"] for f in filings):
        raise efd_check.unexpected(response, f"some entries in the {year} list have no DocID or FilingType")
    return filings, http_codes(response)


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


def download_report(web, filing):
    """GET one report's PDF and save it. Returns the saved path."""
    url = BASE_URL + PTR_PATH.format(year=filing["year"], doc_id=filing["doc_id"])
    response = web.request("GET", url)
    check_response(response)
    if not response.content.startswith(b"%PDF"):
        raise efd_check.unexpected(response, f"report {filing['doc_id']} is not a PDF file")
    path = OUTPUT_DIR / "pdfs" / f"{filing['doc_id']}.pdf"
    path.write_bytes(response.content)
    filing["url"] = url
    filing["pdf_bytes"] = len(response.content)
    return path


def read_pdf_text(path):
    """All the text in a PDF, page by page (empty for scanned pages)."""
    import pdfplumber   # imported here so the rest of the script works without it
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


# ---------------------------------------------------------------------- main

ADVICE = {
    "BLOCKED": ["The House site refused the script. Try one more run later; if it is blocked",
                "again, stop there and share this result."],
    "SITE_DOWN": ["The House site had a server error or showed a maintenance page. Try again later."],
    "CHANGED": ["A file did not look the way the script expects, so the House site may work",
                "differently now. Keep the output folder and share it."],
}


def main():
    parser = argparse.ArgumentParser(description="Check whether this computer can read the "
                                                 "House Clerk's financial disclosure site.")
    parser.add_argument("--email", help="your contact email, sent in the User-Agent")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    ran_on = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "your computer"
    started = datetime.now(timezone.utc)
    (OUTPUT_DIR / "pdfs").mkdir(parents=True, exist_ok=True)
    for old in list(OUTPUT_DIR.glob("*.json")) + list(OUTPUT_DIR.glob("*.xml")) + \
            list((OUTPUT_DIR / "pdfs").glob("*")):
        old.unlink()   # results of an earlier run

    print(f"House access check - {PROJECT} {VERSION}")
    print(f"Running on: {ran_on}")

    log = StepLog()
    counts = {"filings_this_year": 0, "trade_reports": 0, "electronic": 0, "paper": 0,
              "downloaded": 0, "with_text": 0, "transactions_seen": 0}
    reports, failure, failed_step, list_year = [], None, None, None

    email = (args.email or os.environ.get("EFD_CONTACT_EMAIL") or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        print("\nNo valid contact email was given, so nothing was sent to the House site.")
        status, outcome = NOT_RUN, "- no contact email given"
    else:
        print("Contact email: set (it goes in the User-Agent and is not saved in output)\n")
        web = efd_check.PoliteSession(f"{PROJECT}/{VERSION} (non-commercial public transparency "
                                      f"project; contact: {email})")
        try:
            log.begin(1)
            this_year = date.today().year
            filings, codes = download_list(web, this_year)
            list_year = this_year
            if filings is None:   # early January: this year's list may not exist yet
                filings, more = download_list(web, this_year - 1)
                codes += more
                list_year = this_year - 1
                if filings is None:
                    raise StepFailed(CHANGED, f"no list of filings for {this_year} or {this_year - 1} (HTTP 404)")
            types = {}
            for filing in filings:
                types[filing["filing_type"]] = types.get(filing["filing_type"], 0) + 1
            counts["filings_this_year"] = len(filings)
            log.end(OK, f"{len(filings)} filings in the {list_year} list, by type: "
                        + ", ".join(f"{t} {n}" for t, n in sorted(types.items(), key=lambda x: -x[1])), codes)

            log.begin(2)
            ptrs = [f for f in filings if f["filing_type"] == "P"]
            electronic = [f for f in ptrs if is_electronic(f["doc_id"])]
            paper = [f for f in ptrs if not is_electronic(f["doc_id"])]
            if not ptrs:
                raise StepFailed(CHANGED, "no filings of type P (Periodic Transaction Report) in the list")
            counts.update(trade_reports=len(ptrs), electronic=len(electronic), paper=len(paper))
            dates = [d for d in (parse_filing_date(f["filing_date"]) for f in ptrs) if d]
            newest = max(dates) if dates else None
            log.end(OK, f"{len(ptrs)} trade reports: {len(electronic)} electronic, {len(paper)} scanned"
                        + (f"; newest filed {newest:%m/%d/%Y}" if newest else ""))
            save_json("trade_reports_list.json", newest_first(ptrs))

            log.begin(3)
            picked = one_per_member(electronic, NEW_ELECTRONIC)
            for filing in picked:
                filing["path"] = download_report(web, filing)
                filing["kind"] = "electronic"
                reports.append(filing)
            log.end(OK if picked else SKIPPED, f"{len(picked)} downloaded" if picked else "none in the list")

            log.begin(4)
            picked = one_per_member(paper, PAPER)
            for filing in picked:
                filing["path"] = download_report(web, filing)
                filing["kind"] = "scanned"
                reports.append(filing)
            log.end(OK if picked else SKIPPED, f"{len(picked)} downloaded" if picked else "none in the list")

            log.begin(5)
            older_count, notes = 0, []
            for year in OLDER_YEARS:
                old_filings, _ = download_list(web, year, save=False)
                if old_filings is None:
                    notes.append(f"no {year} list")
                    continue
                old_electronic = [f for f in old_filings if f["filing_type"] == "P" and is_electronic(f["doc_id"])]
                for filing in one_per_member(old_electronic, PER_OLDER_YEAR):
                    filing["path"] = download_report(web, filing)
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
            else:
                for filing in reports:
                    pages = read_pdf_text(filing["path"])
                    text = "\n".join(pages)
                    (OUTPUT_DIR / "pdfs" / f"{filing['doc_id']}.txt").write_text(text, encoding="utf-8")
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
                    raise StepFailed(CHANGED, "none of the downloaded PDFs has any text")
                log.end(OK, detail)

        except StepFailed as error:
            failure, failed_step = error, log.current
            log.end(error.status, error.detail, http_codes(error.response))
            log.mark_rest(NOT_RUN)
            if error.response is not None:
                (OUTPUT_DIR / "problem_page.html").write_bytes(error.response.content)
        except Exception as error:   # something the script did not expect
            failure = StepFailed(CHANGED, f"unexpected problem: {short_error(error)}")
            failed_step = log.current
            log.end(CHANGED, failure.detail)
            log.mark_rest(NOT_RUN)
            print("\nTechnical details (useful for fixing the script):")
            traceback.print_exc(file=sys.stdout)

        if failure is not None:
            status = failure.status
            outcome = f"at step {failed_step} ({STEP_NAMES[failed_step]}) - {failure.detail}"
        else:
            status = OK
            outcome = (f"- {counts['trade_reports']} trade reports in the {list_year} list "
                       f"({counts['electronic']} electronic, {counts['paper']} scanned); "
                       f"{counts['downloaded']} downloaded, {counts['with_text']} with text, "
                       f"about {counts['transactions_seen']} transactions seen")

    for filing in reports:
        filing["path"] = str(Path(filing["path"]).relative_to(OUTPUT_DIR))
    verdict_line = f"VERDICT: {status} {outcome} (ran on {ran_on})"
    save_json("reports.json", reports)
    save_json("summary.json", {
        "verdict": status,
        "verdict_line": verdict_line,
        "ran_on": ran_on,
        "started_at_utc": started.isoformat(timespec="seconds"),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "list_year": list_year,
        "steps": log.steps,
        "counts": counts,
        "script": f"{PROJECT} {VERSION}",
        "python": sys.version.split()[0],
    })

    print()
    advice = ADVICE.get(status)
    if advice:
        print("\n".join(advice))
    print(f"Saved in {shown(OUTPUT_DIR)}: this year's list, {len(reports)} PDFs with their text, "
          "reports.json, summary.json")
    print(verdict_line)
    return 0 if status == OK else 1


if __name__ == "__main__":
    sys.exit(main())
