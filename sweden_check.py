"""
sweden_check.py - access test for Swedish executives' trades (Finansinspektionen's PDMR register).

In the EU, executives and board members of listed companies ("persons discharging
managerial responsibilities", PDMRs) must report their trades in their company's
shares within three business days (Market Abuse Regulation, Article 19, since
3 July 2016). Sweden's financial regulator, Finansinspektionen (FI), publishes
every notification in a public register with an export to a text file. This
script tries that register:

  1. read the site's robots.txt (the rules it gives to scripts) and check that
     the register is not off limits
  2. open the register's search page
  3. export the notifications published in the last 7 days and read the file
  4. export one month from 2016 to see that the history is there

Each step gets one status, as in efd_check.py: OK, BLOCKED, SITE_DOWN or
CHANGED (NOT_RUN for steps that never ran). FI says it may limit heavy users,
so this script sends only four requests, 2-3 seconds apart, with a User-Agent
that names the project and gives a contact email.

Run it (with your own email):
    python sweden_check.py --email you@example.com
On GitHub Actions the email comes from the EFD_CONTACT_EMAIL secret instead.

Results are saved in the "output/sweden" folder. The last line printed is the
verdict. Exit code: 0 if OK, 1 otherwise.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import traceback
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib import robotparser

import efd_check   # the polite web session and the status checks are shared with the Senate test
from efd_check import CHANGED, NOT_RUN, OK, StepFailed, check_response, http_codes, short_error


# ------------------------------------------------------------------ settings

PROJECT = "insider-tracking"
VERSION = "0.1"

SITE = "https://marknadssok.fi.se"
ROBOTS = SITE + "/robots.txt"
SEARCH_PAGE = SITE + "/Publiceringsklient/en-GB/Search/Start/Insyn"
# The register's own export ("Export" button on the search page): a semicolon-separated
# text file. Dates are YYYY-MM-DD; the filter is on the publication date.
EXPORT = (SITE + "/Publiceringsklient/en-GB/Search/Search?SearchFunctionType=Insyn"
          "&Publiceringsdatum.From={start:%Y-%m-%d}&Publiceringsdatum.To={end:%Y-%m-%d}&button=export")

RECENT_DAYS = 7
HISTORY_MONTH = (date(2016, 7, 3), date(2016, 7, 31))   # the register starts on 3 July 2016
SAMPLE_ROWS = 300
BYTES_PER_ROW = 300       # rough database size of one saved trade, with its indexes

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "sweden"

STEP_NAMES = {
    1: "Read the site's rules for scripts",
    2: "Open the register's search page",
    3: "Export the last 7 days",
    4: "Export one month from 2016",
}
TOTAL_STEPS = len(STEP_NAMES)

# Column names of the export (English version); the Swedish version has other names.
EXPECTED_COLUMNS = ("Publication date", "Issuer", "Person discharging managerial responsibilities",
                    "Position", "Nature of transaction", "ISIN", "Transaction date", "Volume",
                    "Price", "Currency")


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
    try:
        return str(path.relative_to(Path(__file__).resolve().parent))
    except ValueError:
        return str(path)


def save_json(filename, data):
    with open(OUTPUT_DIR / filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def get_page(web, url, what):
    """GET a page or file and stop with the right status if it is a block or an error."""
    response = web.request("GET", url)
    check_response(response)
    return response


def decode_export(content):
    """The export is UTF-16 text (with or without a byte-order mark); fall back to UTF-8."""
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    if len(content) >= 2 and content[1:2] == b"\x00":
        return content.decode("utf-16-le")
    return content.decode("utf-8-sig")


def read_export(response):
    """Rows of the export as dicts keyed by the header row (and the header itself)."""
    kind = response.headers.get("Content-Type", "")
    text = decode_export(response.content)
    if "<html" in text[:2000].lower():
        raise efd_check.unexpected(response, "the export came back as a web page, not a data file")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    try:
        header = [column.strip() for column in next(reader)]
    except StopIteration:
        raise StepFailed(CHANGED, "the export is empty (not even a header row)", response) from None
    while header and not header[-1]:
        header.pop()          # every line ends with a semicolon, which makes an empty last column
    missing = [c for c in EXPECTED_COLUMNS if c not in header]
    if missing:
        raise StepFailed(CHANGED, f"the export lacks the column(s) {', '.join(missing)} "
                                  f"(it has: {', '.join(header[:8])}...)", response)
    rows = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        rows.append(dict(zip(header, [v.strip() for v in values])))
    return header, rows, kind


def to_number(text):
    try:
        return float(str(text).replace(" ", "").replace("\xa0", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------- steps

def step1_robots(web):
    """robots.txt is a site's list of what scripts may and may not read."""
    response = web.request("GET", ROBOTS)
    codes = http_codes(response)
    if response.status_code == 404:
        return "no robots.txt (no rules given, so the register may be read)", codes
    check_response(response)
    parser = robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    (OUTPUT_DIR / "robots.txt").write_text(response.text, encoding="utf-8")
    allowed = parser.can_fetch("*", SEARCH_PAGE) and parser.can_fetch("*", EXPORT.format(
        start=date(2026, 1, 1), end=date(2026, 1, 2)))
    if not allowed:
        raise StepFailed(efd_check.BLOCKED, "robots.txt says scripts may not read the register "
                                            "(saved as robots.txt)", response)
    delay = parser.crawl_delay("*")
    note = f", asks for {delay} s between requests" if delay else ""
    return f"robots.txt allows the register{note}", codes


def step2_search_page(web):
    response = get_page(web, SEARCH_PAGE, "the search page")
    text = response.text
    if "managerial responsibilities" not in text and "ledande ställning" not in text:
        raise efd_check.unexpected(response, "the search page does not mention managers' "
                                             "transactions")
    total = None
    match = re.search(r"of\s+([\d\s,.]+?)\s+result", text)
    if match:
        total = to_number(match.group(1).replace(".", "").replace(",", ""))
    return total, http_codes(response)


def step3_recent(web, today):
    start, end = today - timedelta(days=RECENT_DAYS), today
    response = get_page(web, EXPORT.format(start=start, end=end), "the export")
    header, rows, kind = read_export(response)
    (OUTPUT_DIR / "export_recent.txt").write_bytes(response.content[:2_000_000])
    return header, rows, kind, len(response.content), http_codes(response)


def step4_history(web):
    start, end = HISTORY_MONTH
    response = get_page(web, EXPORT.format(start=start, end=end), "the export")
    header, rows, kind = read_export(response)
    return len(rows), http_codes(response)


def describe(rows):
    """What the recent rows hold: kinds of transaction, instruments, positions, examples."""
    nature = Counter(r.get("Nature of transaction", "") for r in rows)
    instruments = Counter(r.get("Instrument type", r.get("Intrument type", "")) for r in rows)
    positions = Counter(r.get("Position", "") for r in rows)
    venues = Counter(r.get("Trading venue", "") for r in rows)
    currencies = Counter(r.get("Currency", "") for r in rows)
    status = Counter(r.get("Status", "") for r in rows)
    without_price = sum(1 for r in rows if to_number(r.get("Price")) in (None, 0.0))
    issuers = {r.get("Issuer") for r in rows}
    people = {r.get("Person discharging managerial responsibilities") for r in rows}
    return {
        "rows": len(rows),
        "issuers": len(issuers), "people": len(people),
        "nature_of_transaction": dict(nature.most_common()),
        "instrument_types": dict(instruments.most_common()),
        "positions": dict(positions.most_common(15)),
        "trading_venues": dict(venues.most_common(10)),
        "currencies": dict(currencies.most_common()),
        "status": dict(status.most_common()),
        "rows_without_price": without_price,
        "date_examples": [r.get("Transaction date") for r in rows[:5]],
        "publication_examples": [r.get("Publication date") for r in rows[:3]],
        "volume_examples": [r.get("Volume") for r in rows[:5]],
        "price_examples": [r.get("Price") for r in rows[:5]],
    }


# ---------------------------------------------------------------------- main

ADVICE = {
    "BLOCKED": ["Finansinspektionen's site refused the script, or its robots.txt rules it out.",
                "Do not try to get around this. Stop and share this result; FI can be asked",
                "by email (finansinspektionen@fi.se) whether a daily export is acceptable."],
    "SITE_DOWN": ["The site had a server error. Try again later."],
    "CHANGED": ["A page or file did not look the way the script expects. Keep the output folder",
                "and share it."],
}


def main():
    parser = argparse.ArgumentParser(description="Check whether this computer can read Swedish "
                                                 "executives' trades from Finansinspektionen.")
    parser.add_argument("--email", help="your contact email, sent in the User-Agent")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    ran_on = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "your computer"
    started = datetime.now(timezone.utc)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in list(OUTPUT_DIR.glob("*.json")) + list(OUTPUT_DIR.glob("*.csv")) + \
            list(OUTPUT_DIR.glob("*.txt")):
        old.unlink()

    print(f"Sweden (Finansinspektionen) access check - {PROJECT} {VERSION}")
    print(f"Running on: {ran_on}")

    log = StepLog()
    info, failure, failed_step = {}, None, None
    email = (args.email or os.environ.get("EFD_CONTACT_EMAIL") or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        print("\nNo valid contact email was given, so nothing was sent to Finansinspektionen.")
        status, outcome = NOT_RUN, "- no contact email given"
    else:
        print("Contact email: set (it goes in the User-Agent and is not saved in output)\n")
        web = efd_check.PoliteSession(f"{PROJECT}/{VERSION} (non-commercial public transparency "
                                      f"project; contact: {email})")
        try:
            log.begin(1)
            note, codes = step1_robots(web)
            info["robots"] = note
            log.end(OK, note, codes)

            log.begin(2)
            total, codes = step2_search_page(web)
            info["notifications_in_register"] = total
            log.end(OK, f"{total:,.0f} notifications in the register since 2016" if total
                    else "search page loads", codes)

            log.begin(3)
            header, rows, kind, size, codes = step3_recent(web, date.today())
            info["columns"] = header
            info["content_type"] = kind
            info["recent"] = describe(rows)
            if rows:
                with open(OUTPUT_DIR / "recent_examples.csv", "w", newline="", encoding="utf-8") as file:
                    writer = csv.DictWriter(file, fieldnames=header, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(rows[:SAMPLE_ROWS])
            per_year = len(rows) * 365 / RECENT_DAYS
            info["storage_estimate_mb_per_year"] = round(per_year * BYTES_PER_ROW / 1e6, 1)
            log.end(OK, f"{len(rows):,} rows ({size / 1000:.0f} kB, {len(header)} columns) by "
                        f"{info['recent']['people']:,} people in {info['recent']['issuers']:,} "
                        f"companies", codes)

            log.begin(4)
            old_rows, codes = step4_history(web)
            info["history_month_rows"] = old_rows
            if not old_rows:
                raise StepFailed(CHANGED, "the export for July 2016 is empty: no history")
            log.end(OK, f"{old_rows:,} rows published in July 2016", codes)

        except StepFailed as error:
            failure, failed_step = error, log.current
            log.end(error.status, error.detail, http_codes(error.response))
            log.mark_rest(NOT_RUN)
            if error.response is not None:
                (OUTPUT_DIR / "problem_page.html").write_bytes(error.response.content[:500_000])
        except Exception as error:
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
            recent = info["recent"]
            outcome = (f"- {recent['rows']:,} notifications in the last {RECENT_DAYS} days, "
                       f"{info['history_month_rows']:,} in July 2016; about "
                       f"{info['storage_estimate_mb_per_year']} MB a year")

    verdict_line = f"VERDICT: {status} {outcome} (ran on {ran_on})"
    save_json("summary.json", {
        "verdict": status, "verdict_line": verdict_line, "ran_on": ran_on,
        "started_at_utc": started.isoformat(timespec="seconds"),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": log.steps, **info,
        "script": f"{PROJECT} {VERSION}", "python": sys.version.split()[0],
    })
    print()
    advice = ADVICE.get(status)
    if advice:
        print("\n".join(advice))
    print(f"Saved in {shown(OUTPUT_DIR)}: summary.json, recent_examples.csv, export_recent.txt, robots.txt")
    print(verdict_line)
    return 0 if status == OK else 1


if __name__ == "__main__":
    sys.exit(main())
