"""
sweden.py - can a script read Swedish executives' trades (Finansinspektionen's PDMR register)?

In the EU, executives and board members of listed companies ("persons discharging managerial
responsibilities", PDMRs) must report their trades in their company's shares within three
business days (Market Abuse Regulation, Article 19, since 3 July 2016). Sweden's financial
regulator, Finansinspektionen (FI), publishes every notification in a public register with an
export to a text file. This check tries that register:

  1. read the site's robots.txt (the rules it gives to scripts) and check that the register
     is not off limits
  2. open the register's search page
  3. export the notifications published in the last 7 days and read the file
  4. export one month from 2016 to see that the history is there

FI says it may limit heavy users, so this check sends only four requests. Results are saved in
"output/sweden".
"""

import csv
import io
import re
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

from ..core.errors import SiteStop
from ..core.status import BLOCKED, CHANGED, OK
from ..sources.sweden.parsing import decode_export
from ..sources.sweden.site import FiEndpoints
from ..web.checks import BlockPageCheck, unexpected
from ..web.describe import http_codes
from ..web.robots import read_rules
from .framework import PACING, Check, CheckDefinition, CheckRunner, common_summary, script_fields, shown, times

RECENT_DAYS = 7
HISTORY_MONTH = (date(2016, 7, 3), date(2016, 7, 31))   # the register starts on 3 July 2016
SAMPLE_ROWS = 300
BYTES_PER_ROW = 300       # rough database size of one saved trade, with its indexes

# Column names of the export (English version); the Swedish version has other names.
EXPECTED_COLUMNS = ("Publication date", "Issuer", "Person discharging managerial responsibilities",
                    "Position", "Nature of transaction", "ISIN", "Transaction date", "Volume",
                    "Price", "Currency")

DEFINITION = CheckDefinition(
    title="Sweden (Finansinspektionen) access check",
    description="Check whether this computer can read Swedish executives' trades from Finansinspektionen.",
    steps=("Read the site's rules for scripts", "Open the register's search page", "Export the last 7 days",
           "Export one month from 2016"),
    site="Finansinspektionen",
    folder="sweden",
    advice={
        "BLOCKED": ["Finansinspektionen's site refused the script, or its robots.txt rules it out.",
                    "Do not try to get around this. Stop and share this result; FI can be asked",
                    "by email (finansinspektionen@fi.se) whether a daily export is acceptable."],
        "SITE_DOWN": ["The site had a server error. Try again later."],
        "CHANGED": ["A page or file did not look the way the script expects. Keep the output folder",
                    "and share it."],
    },
)


def to_number(text):
    try:
        return float(str(text).replace(" ", "").replace("\xa0", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def read_export(response):
    """Rows of the export as dicts keyed by the header row (and the header itself)."""
    kind = response.headers.get("Content-Type", "")
    text = decode_export(response.content)
    if "<html" in text[:2000].lower():
        raise unexpected(response, "the export came back as a web page, not a data file")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    try:
        header = [column.strip() for column in next(reader)]
    except StopIteration:
        raise SiteStop(CHANGED, "the export is empty (not even a header row)", response) from None
    while header and not header[-1]:
        header.pop()          # every line ends with a semicolon, which makes an empty last column
    missing = [c for c in EXPECTED_COLUMNS if c not in header]
    if missing:
        raise SiteStop(CHANGED, f"the export lacks the column(s) {', '.join(missing)} "
                                f"(it has: {', '.join(header[:8])}...)", response)
    rows = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        rows.append(dict(zip(header, [v.strip() for v in values])))
    return header, rows, kind


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


class SwedenCheck(Check):
    def __init__(self, endpoints=FiEndpoints(), pacing=PACING):
        self.endpoints = endpoints
        self.definition = replace(DEFINITION, pacing=pacing)
        self.check = BlockPageCheck(name_the_page=False)

    def clear(self, folder):
        for old in list(folder.glob("*.json")) + list(folder.glob("*.csv")) + list(folder.glob("*.txt")):
            old.unlink()

    def export(self, client, start, end):
        response = client.get(self.endpoints.export.format(start=start, end=end), check=self.check)
        return response, read_export(response)

    def run_steps(self, run, client):
        log, info = run.log, run.info

        log.begin(1)
        response = client.get(self.endpoints.robots, check=self.check, ok_codes=(404,))
        codes = http_codes(response)
        if response.status_code == 404:
            note = "no robots.txt (no rules given, so the register may be read)"
        else:
            (run.folder / "robots.txt").write_text(response.text, encoding="utf-8")
            rules = read_rules(response.text, [self.endpoints.search_page, self.endpoints.export.format(
                start=date(2026, 1, 1), end=date(2026, 1, 2))])
            if not rules.allows_all:
                raise SiteStop(BLOCKED, "robots.txt says scripts may not read the register "
                                        "(saved as robots.txt)", response)
            note = "robots.txt allows the register" + (f", asks for {rules.delay} s between requests"
                                                       if rules.delay else "")
        info["robots"] = note
        log.end(OK, note, codes)

        log.begin(2)
        response = client.get(self.endpoints.search_page, check=self.check)
        text = response.text
        if "managerial responsibilities" not in text and "ledande ställning" not in text:
            raise unexpected(response, "the search page does not mention managers' transactions")
        total = None
        match = re.search(r"of\s+([\d\s,.]+?)\s+result", text)
        if match:
            total = to_number(match.group(1).replace(".", "").replace(",", ""))
        info["notifications_in_register"] = total
        log.end(OK, f"{total:,.0f} notifications in the register since 2016" if total else "search page loads",
                http_codes(response))

        log.begin(3)
        start, end = run.today - timedelta(days=RECENT_DAYS), run.today
        response, (header, rows, kind) = self.export(client, start, end)
        (run.folder / "export_recent.txt").write_bytes(response.content[:2_000_000])
        info["columns"] = header
        info["content_type"] = kind
        info["recent"] = describe(rows)
        if rows:
            with open(run.folder / "recent_examples.csv", "w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=header, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows[:SAMPLE_ROWS])
        per_year = len(rows) * 365 / RECENT_DAYS
        info["storage_estimate_mb_per_year"] = round(per_year * BYTES_PER_ROW / 1e6, 1)
        log.end(OK, f"{len(rows):,} rows ({len(response.content) / 1000:.0f} kB, {len(header)} columns) by "
                    f"{info['recent']['people']:,} people in {info['recent']['issuers']:,} companies",
                http_codes(response))

        log.begin(4)
        response, (header, old_rows, kind) = self.export(client, *HISTORY_MONTH)
        info["history_month_rows"] = len(old_rows)
        if not old_rows:
            raise SiteStop(CHANGED, "the export for July 2016 is empty: no history")
        log.end(OK, f"{len(old_rows):,} rows published in July 2016", http_codes(response))

    def outcome(self, run):
        info = run.info
        return (f"- {info['recent']['rows']:,} notifications in the last {RECENT_DAYS} days, "
                f"{info['history_month_rows']:,} in July 2016; about "
                f"{info['storage_estimate_mb_per_year']} MB a year")

    def summary(self, run, verdict_line):
        return {**common_summary(run, verdict_line), **times(run), "steps": run.log.steps, **run.info,
                **script_fields()}

    def saved_line(self, run):
        return f"Saved in {shown(run.folder)}: summary.json, recent_examples.csv, export_recent.txt, robots.txt"


def command():
    """`python -m insider_tracking check sweden`."""
    return CheckRunner(SwedenCheck())
