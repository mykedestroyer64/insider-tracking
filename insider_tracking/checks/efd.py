"""
efd.py - can a script read the US Senate financial disclosure site (eFD)?

It does what a person does in a browser:
  1. open the home page and read its security token (the CSRF token)
  2. accept the site's usage agreement
  3. search for Periodic Transaction Reports received in the last 30 days
  4. pick the newest electronic report (scanned paper reports are skipped)
  5. open that report and read its transactions table

Run it with your own email:   python -m insider_tracking check efd --email you@example.com
On GitHub Actions the email comes from the EFD_CONTACT_EMAIL secret instead. Results are
saved in the "output" folder.
"""

import re
from dataclasses import replace
from datetime import date, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..core.status import BLOCKED, CHANGED, OK, SITE_DOWN, SKIPPED
from ..web.checks import BlockPageCheck, unexpected
from ..web.describe import http_codes, path_of
from ..sources.senate.parsing import clean, kind_of, result_link, text_of, transactions_table
from ..sources.senate.site import EfdEndpoints, EfdSite, search_results, sent_back_to_agreement, total_of
from .framework import PACING, Check, CheckDefinition, CheckRunner, common_summary, script_fields, times, write_json

DAYS_BACK = 30         # search reports received in the last 30 days
PAGE_SIZE = 25         # how many search results to ask for
OUTPUT_FILES = ["search_results.json", "report.html", "transactions.json", "summary.json", "problem_page.html"]
FIELDS = ["transaction_date", "owner", "ticker", "asset_name", "asset_type", "transaction_type", "amount", "comment"]

DEFINITION = CheckDefinition(
    title="eFD access check",
    description="Check whether this computer can read the US Senate eFD site.",
    steps=("Open the eFD home page", "Accept the usage agreement", "Search transaction reports",
           "Pick the newest electronic report", "Read its transactions table"),
    site="eFD",
    label_width=38,
    http_rule=True,
    http_status=True,
    problem_page_limit=None,
    advice={
        (BLOCKED, "your computer"): [
            "eFD refused the script. Check that your VPN app is connected to a US server and",
            "that https://efdsearch.senate.gov/search/home/ opens in your browser right now.",
            "If the browser works but the script is still BLOCKED, the block is aimed at the",
            "script, not at your location: stop here and share this result.",
        ],
        (BLOCKED, "GitHub Actions"): [
            "eFD refused GitHub's server. You can try one more run later.",
            "If it is blocked again, stop there and share this result.",
        ],
        SITE_DOWN: ["eFD had a server error or showed a maintenance page. Try again later."],
        CHANGED: [
            "A page did not look the way the script expects, so eFD may work differently now.",
            "See \"If the verdict is CHANGED\" in README.md and keep output/problem_page.html.",
        ],
    },
)


def window(today):
    """The search window: received in the last 30 days, up to midnight tonight (today counts too)."""
    return today - timedelta(days=DAYS_BACK), today + timedelta(days=1)


def read_result_row(row, base_url):
    """One search result row as shown on the site, or None if it looks different."""
    # Each row is a list: first name, last name, filer type, report link (HTML), date received
    if not isinstance(row, list) or len(row) < 5:
        return None
    link = result_link(row[3])
    if link is None:
        return None
    href = link["href"]
    return {
        "first_name": text_of(row[0]),
        "last_name": text_of(row[1]),
        "filer_type": text_of(row[2]),
        "title": clean(link.get_text(" ")),
        "date_received": text_of(row[4]),
        "url": urljoin(base_url, href),
        "kind": kind_of(href),
    }


def received_date(report):
    """The report's date received as a date (or a very old date if unreadable)."""
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", report["date_received"])
    if not match:
        return date.min
    month, day, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return date.min


class EfdCheck(Check):
    def __init__(self, endpoints=EfdEndpoints(), pacing=PACING):
        self.endpoints = endpoints
        self.definition = replace(DEFINITION, pacing=pacing)

    def clear(self, folder):
        for name in OUTPUT_FILES:
            (folder / name).unlink(missing_ok=True)

    def setup(self, run):
        run.info.update(counts={"reports_found": None, "reports_downloaded": 0, "electronic": 0,
                                "paper": 0, "other": 0, "transactions_parsed": 0}, newest=None)

    def intro(self, run):
        date_from, _ = window(run.today)
        return [f"Search window: reports received {date_from:%m/%d/%Y} to {run.today:%m/%d/%Y}"]

    def no_email(self, run):
        hint = ("Add a repository secret named EFD_CONTACT_EMAIL (see README.md, part B)."
                if run.ran_on == "GitHub Actions"
                else "Add your email at the end of the command, like this:  --email you@example.com")
        return [f"\nNo valid contact email was given, so nothing was sent to {self.definition.site}.", hint]

    # ---------------------------------------------------------------- steps

    def run_steps(self, run, client):
        log, counts = run.log, run.info["counts"]
        site = EfdSite(client, self.endpoints, check=BlockPageCheck(name_the_page=False))
        date_from, date_to = window(run.today)

        log.begin(1)
        home = site.home_page()
        page = BeautifulSoup(home.text, "html.parser")
        token = site.token_on(home)
        if not token:
            raise unexpected(home, "no csrfmiddlewaretoken field and no csrftoken cookie")
        if page.find("input", attrs={"name": "prohibition_agreement"}):
            detail = "found the agreement form and its token"
        else:
            detail = "found the token, but no 'prohibition_agreement' checkbox"
        log.end(OK, detail, http_codes(home))

        log.begin(2)
        cookies_before = client.cookie_values()
        response = site.agreement_answer(token)
        page = BeautifulSoup(response.text, "html.parser")
        if page.find("input", attrs={"name": "prohibition_agreement"}):
            raise unexpected(response, "the agreement page came back, so it was not accepted")
        if path_of(response.url) != "/search/":
            raise unexpected(response, f"expected to land on /search/ but got {path_of(response.url)}")
        detail = ("now on /search/, session cookie updated" if client.cookie_values() != cookies_before
                  else "now on /search/ (no cookie changed)")
        log.end(OK, detail, http_codes(response))

        log.begin(3)
        response = site.search_page(date_from, date_to, length=PAGE_SIZE,
                                    token=client.cookie("csrftoken", "csrf") or token)
        if sent_back_to_agreement(response):
            raise unexpected(response, "sent back to the agreement page, so the session was not kept")
        results = search_results(response)
        write_json(run.folder / "search_results.json", results)
        reports = [read_result_row(row, self.endpoints.base) for row in results["data"]]
        if any(report is None for report in reports):
            raise unexpected(response, "a result row does not have the expected columns "
                                       "(first name, last name, filer type, report link, date)")
        total = total_of(results)
        kinds = [report["kind"] for report in reports]
        counts.update(reports_found=total, reports_downloaded=len(reports), electronic=kinds.count("electronic"),
                      paper=kinds.count("paper"), other=kinds.count("other"))
        detail = (f"{total} reports found, got {len(reports)}: "
                  f"{counts['electronic']} electronic, {counts['paper']} paper")
        if counts["other"]:
            detail += f", {counts['other']} other"
        log.end(OK, detail, http_codes(response))

        log.begin(4)
        electronic = [report for report in reports if report["kind"] == "electronic"]
        if not electronic:
            log.end(SKIPPED, "no electronic report in the results, so nothing to open")
            log.mark_rest(SKIPPED, "nothing to open")
            return
        newest = run.info["newest"] = max(electronic, key=received_date)
        detail = (f"{newest['first_name']} {newest['last_name']}, received "
                  f"{newest['date_received']} [filer type: {newest['filer_type']}]")
        if total > len(reports):
            detail += f", the newest of the {len(reports)} results downloaded"
        log.end(OK, detail)

        log.begin(5)
        transactions, detail, codes = self.read_report(run, site, newest)
        counts["transactions_parsed"] = len(transactions)
        log.end(OK, detail, codes)

    def read_report(self, run, site, report):
        """GET the report page, save it, and read its transactions table."""
        response = site.report(report["url"], ok_codes=())
        if sent_back_to_agreement(response):
            raise unexpected(response, "sent back to the agreement page, so the session was not kept")
        (run.folder / "report.html").write_bytes(response.content)
        table = transactions_table(response.text)
        if table is None:
            raise unexpected(response, "no transactions table (with Transaction Date, Ticker "
                                       "and Amount columns) on the report page")
        headers, fields, rows, odd_rows = table
        transactions = []
        for texts in rows:
            transaction = {field: "" for field in FIELDS}
            for field, text in zip(fields, texts):
                if field:   # skip columns we don't use, such as "#"
                    transaction[field] = text
            transactions.append(transaction)
        if not transactions:
            raise unexpected(response, "the transactions table has no rows that could be read")
        write_json(run.folder / "transactions.json", {
            "report": {key: report[key] for key in
                       ("first_name", "last_name", "filer_type", "title", "date_received", "url")},
            "columns_on_page": headers,
            "transaction_count": len(transactions),
            "transactions": transactions,
        })
        detail = f"{len(transactions)} transactions parsed"
        if odd_rows:
            detail += f" ({odd_rows} rows had a different number of cells and were skipped)"
        return transactions, detail, http_codes(response)

    # ------------------------------------------------------------ summing up

    def outcome(self, run):
        counts = run.info["counts"]
        if run.info["newest"] is None:
            run.advice = ["Access works, but there was no electronic report to read.",
                          "Run it again in a few days to also test reading a report."]
            if counts["reports_found"] == 0:
                return f"- 0 reports found in the last {DAYS_BACK} days, so nothing to parse"
            return f"- {counts['reports_found']} reports found, none of them electronic, so no transactions to parse"
        return f"- {counts['reports_found']} reports found, {counts['transactions_parsed']} transactions parsed"

    def summary(self, run, verdict_line):
        date_from, _ = window(run.today)
        return {
            **common_summary(run, verdict_line),
            "github_actions": run.ran_on == "GitHub Actions",
            **times(run),
            "search_window": {"received_from": f"{date_from:%m/%d/%Y}", "received_to": f"{run.today:%m/%d/%Y}"},
            "steps": run.log.steps,
            "counts": run.info["counts"],
            "akamai_reference": run.failure.reference if run.failure else None,
            **script_fields(),
        }

    def saved_line(self, run):
        saved = [name for name in OUTPUT_FILES if (run.folder / name).exists()]
        return f"Saved in the output folder: {', '.join(saved)}"


def command():
    """`python -m insider_tracking check efd`."""
    return CheckRunner(EfdCheck())
