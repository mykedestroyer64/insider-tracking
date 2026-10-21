"""
efd_check.py - step 1 of the insider-tracking project.

Checks whether a script can read the US Senate financial disclosure site
(eFD, https://efdsearch.senate.gov). It does what a person does in a browser:

  1. open the home page and read its security token (the CSRF token)
  2. accept the site's usage agreement
  3. search for Periodic Transaction Reports received in the last 30 days
  4. pick the newest electronic report (scanned paper reports are skipped)
  5. open that report and read its transactions table

Each step gets one status:
  OK         it worked
  BLOCKED    the site refused us (HTTP 403/429 or an Akamai "Access Denied" page)
  SITE_DOWN  the site had a server error or showed a maintenance page
  CHANGED    a page did not look the way this script expects
Steps that never ran because an earlier step failed are marked NOT_RUN.

The script is polite on purpose: it waits 2-3 seconds between requests, gives
every request a time limit, retries at most once, and sends a User-Agent that
names the project and gives a contact email. If the site blocks it, it says so
and stops. It never tries to get around a block.

Run it (with your own email):
    python efd_check.py --email you@example.com
On GitHub Actions the email comes from the EFD_CONTACT_EMAIL secret instead.

Results are saved in the "output" folder next to this file. The last line
printed is the verdict. Exit code: 0 if the verdict is OK, 1 otherwise.
"""

import argparse
import html
import json
import os
import random
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ------------------------------------------------------------------ settings

PROJECT = "insider-tracking"
VERSION = "0.1"

BASE_URL = "https://efdsearch.senate.gov"
HOME_URL = BASE_URL + "/search/home/"          # page with the usage agreement
SEARCH_URL = BASE_URL + "/search/"             # search page, after the agreement
DATA_URL = BASE_URL + "/search/report/data/"   # search results, as JSON

DAYS_BACK = 30         # search reports received in the last 30 days
PAGE_SIZE = 25         # how many search results to ask for
PTR_TYPE = "[11]"      # report type 11 = Periodic Transaction Report

TIMEOUT = (10, 30)     # seconds allowed to connect, and to wait for an answer
PAUSE = (2.0, 3.0)     # seconds to wait between requests (picked at random)
RETRY_EXTRA_PAUSE = 5  # extra seconds to wait before the one retry

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_FILES = ["search_results.json", "report.html", "transactions.json",
                "summary.json", "problem_page.html"]

STEP_NAMES = {
    1: "Open the eFD home page",
    2: "Accept the usage agreement",
    3: "Search transaction reports",
    4: "Pick the newest electronic report",
    5: "Read its transactions table",
}

# Step statuses
OK, BLOCKED, SITE_DOWN, CHANGED = "OK", "BLOCKED", "SITE_DOWN", "CHANGED"
SKIPPED, NOT_RUN = "SKIPPED", "NOT_RUN"

# Words that point to a maintenance page or a bot-check page
MAINTENANCE_WORDS = ["maintenance", "temporarily unavailable",
                     "currently unavailable", "service unavailable"]
BOT_CHECK_WORDS = ["access denied", "sec-if-cpt", "bm-verify"]

# Headers of a report's transactions table (in lower case) -> our field names
COLUMN_FIELDS = {
    "transaction date": "transaction_date",
    "owner": "owner",
    "ticker": "ticker",
    "asset name": "asset_name",
    "asset description": "asset_name",
    "asset type": "asset_type",
    "type": "transaction_type",
    "transaction type": "transaction_type",
    "amount": "amount",
    "comment": "comment",
    "comments": "comment",
}
FIELDS = ["transaction_date", "owner", "ticker", "asset_name", "asset_type",
          "transaction_type", "amount", "comment"]


# ------------------------------------------------------------------- helpers

class StepFailed(Exception):
    """Stops the check. Carries the status, an explanation and the page we got."""

    def __init__(self, status, detail, response=None, reference=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.response = response
        self.reference = reference   # Akamai "Reference #..." text, if any


class PoliteSession:
    """One browser-like session (cookies are kept) that is gentle with the site."""

    def __init__(self, user_agent):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.requests_sent = 0

    def request(self, method, url, **options):
        """Send a request: pause first, use a time limit, retry once if needed."""
        for attempt in (1, 2):
            if self.requests_sent > 0:
                wait = random.uniform(*PAUSE)
                if attempt == 2:
                    wait += RETRY_EXTRA_PAUSE
                time.sleep(wait)
            self.requests_sent += 1
            try:
                response = self.session.request(method, url, timeout=TIMEOUT, **options)
            except requests.RequestException as error:
                if attempt == 1:
                    continue   # no answer (network problem): try once more
                raise StepFailed(SITE_DOWN, "no answer from the site after 2 tries "
                                            f"({short_error(error)})")
            if response.status_code >= 500 and attempt == 1:
                continue       # server error: try once more
            return response

    def cookie(self, *names):
        """Value of the first cookie found with one of these names, or None."""
        for name in names:
            for cookie in self.session.cookies:
                if cookie.name == name:
                    return cookie.value
        return None

    def cookie_values(self):
        return {cookie.name: cookie.value for cookie in self.session.cookies}


class StepLog:
    """Prints each step's result and keeps it for summary.json."""

    def __init__(self):
        self.steps = []
        self.current = 0

    def begin(self, number):
        self.current = number
        label = f"{STEP_NAMES[number]} ".ljust(38, ".")
        print(f"[{number}/5] {label}", end=" ", flush=True)

    def end(self, status, detail="", codes=()):
        codes = list(codes)
        http = f" (HTTP {' -> '.join(str(code) for code in codes)})" if codes else ""
        if detail.startswith("HTTP "):
            http = ""   # the explanation already starts with the HTTP code
        print(f"{status}{http}" + (f" - {detail}" if detail else ""), flush=True)
        self.steps.append({
            "step": self.current,
            "name": STEP_NAMES[self.current],
            "status": status,
            "http_status": codes[-1] if codes else None,
            "http_codes": codes,
            "detail": detail,
        })

    def mark_rest(self, status, detail=""):
        """Give every step after the current one the same status (NOT_RUN or SKIPPED)."""
        for number in range(self.current + 1, 6):
            self.begin(number)
            self.end(status, detail)


def check_response(response):
    """Stop the step if the answer is a block, an outage or an error page."""
    code = response.status_code
    text = html.unescape(response.text)   # Akamai writes "Reference&#32;&#35;..." in HTML codes
    if code == 403 and "CSRF verification failed" in text:
        raise StepFailed(CHANGED, "HTTP 403 'CSRF verification failed': the site did not "
                                  "accept our form token, so its form has probably changed",
                         response)
    reference = akamai_reference(text)
    if reference or code in (403, 429):
        if code == 429:
            what = "Too Many Requests"
        elif "Access Denied" in text:
            what = "Access Denied"
        else:
            what = "Forbidden"
        detail = f"HTTP {code} {what}" + (f", {reference}" if reference else "")
        raise StepFailed(BLOCKED, detail, response, reference)
    if code >= 500:
        raise StepFailed(SITE_DOWN, f"HTTP {code} server error, also after one retry", response)
    if code >= 400:
        raise StepFailed(CHANGED, f"HTTP {code} error page", response)


def akamai_reference(text):
    """If this is Akamai's "Access Denied" page, return its "Reference #..." text."""
    if "Access Denied" not in text:
        return None
    match = re.search(r"Reference\s*#\s*[\w.]+", text)
    return match.group(0) if match else None


def unexpected(response, problem):
    """A page loaded but did not look as expected: pick the status that fits."""
    lower = response.text.lower()
    if any(word in lower for word in MAINTENANCE_WORDS):
        return StepFailed(SITE_DOWN, f"{problem} (the page mentions maintenance)", response)
    if any(word in lower for word in BOT_CHECK_WORDS):
        return StepFailed(BLOCKED, f"{problem} (it looks like a bot-check page)", response)
    return StepFailed(CHANGED, problem, response)


def http_codes(response):
    """Status codes of a response and of any redirects before it, e.g. [302, 200]."""
    if response is None:
        return []
    return [r.status_code for r in response.history] + [response.status_code]


def path_of(url):
    return urlparse(url).path


def clean(text):
    """Turn runs of spaces and line breaks into single spaces."""
    return " ".join(str(text).split())


def text_of(value):
    """Plain text of a value that may contain HTML tags or HTML codes."""
    value = str(value)
    if "<" in value:
        value = BeautifulSoup(value, "html.parser").get_text(" ")
    return clean(html.unescape(value))


def short_error(error):
    """A short, readable description of an error."""
    if isinstance(error, requests.Timeout):
        return "timed out"
    if isinstance(error, requests.exceptions.SSLError):
        return "secure connection failed"
    if isinstance(error, requests.ConnectionError):
        text = str(error).lower()
        if any(hint in text for hint in ("getaddrinfo", "name or service", "nodename",
                                         "name resolution")):
            return "the site's address could not be looked up"
        if "refused" in text:
            return "connection refused"
        if "reset" in text or "aborted" in text:
            return "connection cut off"
        return "could not connect"
    return clean(f"{type(error).__name__}: {error}")[:160]


def save_json(filename, data):
    with open(OUTPUT_DIR / filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------- steps

def step1_open_home(web):
    """GET the home page and read the CSRF token (form field, or cookie)."""
    response = web.request("GET", HOME_URL)
    check_response(response)
    page = BeautifulSoup(response.text, "html.parser")
    field = page.find("input", attrs={"name": "csrfmiddlewaretoken"})
    token = (field.get("value") if field else None) or web.cookie("csrftoken", "csrf")
    if not token:
        raise unexpected(response, "no csrfmiddlewaretoken field and no csrftoken cookie")
    if page.find("input", attrs={"name": "prohibition_agreement"}):
        detail = "found the agreement form and its token"
    else:
        detail = "found the token, but no 'prohibition_agreement' checkbox"
    return token, detail, http_codes(response)


def step2_accept_agreement(web, token):
    """POST the agreement; the site should send us on to /search/."""
    cookies_before = web.cookie_values()
    response = web.request(
        "POST", HOME_URL,
        data={"csrfmiddlewaretoken": token, "prohibition_agreement": "1"},
        headers={"Referer": HOME_URL},
    )
    check_response(response)
    page = BeautifulSoup(response.text, "html.parser")
    if page.find("input", attrs={"name": "prohibition_agreement"}):
        raise unexpected(response, "the agreement page came back, so it was not accepted")
    if path_of(response.url) != "/search/":
        raise unexpected(response, f"expected to land on /search/ but got {path_of(response.url)}")
    if web.cookie_values() != cookies_before:
        detail = "now on /search/, session cookie updated"
    else:
        detail = "now on /search/ (no cookie changed)"
    return detail, http_codes(response)


def step3_search(web, token, date_from, date_to):
    """POST the search form to the JSON endpoint and check the answer's shape."""
    form = {
        "start": "0",
        "length": str(PAGE_SIZE),
        "report_types": PTR_TYPE,
        "filer_types": "[]",                       # [] = every kind of filer
        "submitted_start_date": f"{date_from:%m/%d/%Y} 00:00:00",
        "submitted_end_date": f"{date_to:%m/%d/%Y} 00:00:00",
        "candidate_state": "",
        "senator_state": "",
        "office_id": "",
        "first_name": "",
        "last_name": "",
        "csrfmiddlewaretoken": web.cookie("csrftoken", "csrf") or token,
    }
    # X-Requested-With marks this as a data request, like the site's own search page sends
    response = web.request("POST", DATA_URL, data=form,
                           headers={"Referer": SEARCH_URL, "X-Requested-With": "XMLHttpRequest"})
    check_response(response)
    if path_of(response.url).startswith("/search/home"):
        raise unexpected(response, "sent back to the agreement page, so the session was not kept")
    try:
        results = response.json()
    except ValueError:
        raise unexpected(response, "the search answer was not JSON") from None
    if not isinstance(results, dict) or not isinstance(results.get("data"), list):
        raise unexpected(response, "the search JSON has no 'data' list")
    if results.get("result", "ok") != "ok":
        raise unexpected(response, f"the search JSON says result = {results.get('result')!r}")
    save_json("search_results.json", results)

    reports = [read_result_row(row) for row in results["data"]]
    if any(report is None for report in reports):
        raise unexpected(response, "a result row does not have the expected columns "
                                   "(first name, last name, filer type, report link, date)")
    try:   # total number of matching reports, which can be more than we asked for
        total = int(results.get("recordsFiltered", len(reports)))
    except (TypeError, ValueError):
        total = len(reports)
    return reports, total, http_codes(response)


def read_result_row(row):
    """Turn one search result row into a dict, or None if it looks different."""
    # Each row is a list: first name, last name, filer type, report link (HTML), date received
    if not isinstance(row, list) or len(row) < 5:
        return None
    link = BeautifulSoup(str(row[3]), "html.parser").find("a", href=True)
    if link is None:
        return None
    href = link["href"]
    if "/search/view/ptr/" in href:
        kind = "electronic"       # a web page with a transactions table
    elif "/search/view/paper/" in href:
        kind = "paper"            # scanned images: skipped in this step
    else:
        kind = "other"
    return {
        "first_name": text_of(row[0]),
        "last_name": text_of(row[1]),
        "filer_type": text_of(row[2]),
        "title": clean(link.get_text(" ")),
        "date_received": text_of(row[4]),
        "url": urljoin(BASE_URL, href),
        "kind": kind,
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


def step5_read_report(web, report):
    """GET the report page, save it, and read its transactions table."""
    response = web.request("GET", report["url"], headers={"Referer": SEARCH_URL})
    check_response(response)
    if path_of(response.url).startswith("/search/home"):
        raise unexpected(response, "sent back to the agreement page, so the session was not kept")
    (OUTPUT_DIR / "report.html").write_bytes(response.content)

    page = BeautifulSoup(response.text, "html.parser")
    table, headers, fields = find_transactions_table(page)
    if table is None:
        raise unexpected(response, "no transactions table (with Transaction Date, Ticker "
                                   "and Amount columns) on the report page")
    transactions, odd_rows = read_transactions(table, fields)
    if not transactions:
        raise unexpected(response, "the transactions table has no rows that could be read")

    save_json("transactions.json", {
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


def find_transactions_table(page):
    """Find the table whose headers include Transaction Date, Ticker and Amount."""
    for table in page.find_all("table"):
        header_row = table.find("tr")
        if header_row is None:
            continue
        headers = [clean(cell.get_text(" ")) for cell in header_row.find_all(["th", "td"])]
        fields = [COLUMN_FIELDS.get(header.lower()) for header in headers]
        if {"transaction_date", "ticker", "amount"} <= set(fields):
            return table, headers, fields
    return None, None, None


def read_transactions(table, fields):
    """Read every row under the header row into a dict with our field names."""
    transactions, odd_rows = [], 0
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        if len(cells) != len(fields):
            odd_rows += 1
            continue
        transaction = {field: "" for field in FIELDS}
        for field, cell in zip(fields, cells):
            if field:   # skip columns we don't use, such as "#"
                transaction[field] = clean(cell.get_text(" "))
        transactions.append(transaction)
    return transactions, odd_rows


# ---------------------------------------------------------------------- main

ADVICE = {
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
}


def main():
    parser = argparse.ArgumentParser(description="Check whether this computer can read "
                                                 "the US Senate eFD site.")
    parser.add_argument("--email", help="your contact email, sent in the User-Agent")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")   # never crash on an unusual character
    except AttributeError:
        pass

    ran_on = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "your computer"
    started = datetime.now(timezone.utc)
    today = date.today()
    date_from = today - timedelta(days=DAYS_BACK)
    date_to = today + timedelta(days=1)   # midnight tonight, so today's reports count too

    OUTPUT_DIR.mkdir(exist_ok=True)
    for filename in OUTPUT_FILES:          # remove results of an earlier run
        (OUTPUT_DIR / filename).unlink(missing_ok=True)

    print(f"eFD access check - {PROJECT} {VERSION}")
    print(f"Running on: {ran_on}")
    print(f"Search window: reports received {date_from:%m/%d/%Y} to {today:%m/%d/%Y}")

    log = StepLog()
    counts = {"reports_found": None, "reports_downloaded": 0, "electronic": 0,
              "paper": 0, "other": 0, "transactions_parsed": 0}
    failure, failed_step, newest, advice = None, None, None, []

    email = (args.email or os.environ.get("EFD_CONTACT_EMAIL") or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        print("\nNo valid contact email was given, so nothing was sent to eFD.")
        if ran_on == "GitHub Actions":
            print("Add a repository secret named EFD_CONTACT_EMAIL (see README.md, part B).")
        else:
            print("Add your email at the end of the command, like this:  --email you@example.com")
        status, outcome = NOT_RUN, "- no contact email given"
    else:
        print("Contact email: set (it goes in the User-Agent and is not saved in output)\n")
        web = PoliteSession(f"{PROJECT}/{VERSION} (non-commercial public transparency "
                            f"project; contact: {email})")
        try:
            log.begin(1)
            token, detail, codes = step1_open_home(web)
            log.end(OK, detail, codes)

            log.begin(2)
            detail, codes = step2_accept_agreement(web, token)
            log.end(OK, detail, codes)

            log.begin(3)
            reports, total, codes = step3_search(web, token, date_from, date_to)
            kinds = [report["kind"] for report in reports]
            counts.update(reports_found=total, reports_downloaded=len(reports),
                          electronic=kinds.count("electronic"), paper=kinds.count("paper"),
                          other=kinds.count("other"))
            detail = (f"{total} reports found, got {len(reports)}: "
                      f"{counts['electronic']} electronic, {counts['paper']} paper")
            if counts["other"]:
                detail += f", {counts['other']} other"
            log.end(OK, detail, codes)

            log.begin(4)
            electronic = [report for report in reports if report["kind"] == "electronic"]
            if not electronic:
                log.end(SKIPPED, "no electronic report in the results, so nothing to open")
                log.mark_rest(SKIPPED, "nothing to open")
            else:
                newest = max(electronic, key=received_date)
                detail = (f"{newest['first_name']} {newest['last_name']}, received "
                          f"{newest['date_received']} [filer type: {newest['filer_type']}]")
                if total > len(reports):
                    detail += f", the newest of the {len(reports)} results downloaded"
                log.end(OK, detail)

                log.begin(5)
                transactions, detail, codes = step5_read_report(web, newest)
                counts["transactions_parsed"] = len(transactions)
                log.end(OK, detail, codes)

        except StepFailed as error:
            failure, failed_step = error, log.current
            log.end(error.status, error.detail, http_codes(error.response))
            log.mark_rest(NOT_RUN)
            if error.response is not None:
                (OUTPUT_DIR / "problem_page.html").write_bytes(error.response.content)
        except Exception as error:   # the script tripped over something it did not expect
            failure = StepFailed(CHANGED, f"unexpected problem: {short_error(error)}")
            failed_step = log.current
            log.end(CHANGED, failure.detail)
            log.mark_rest(NOT_RUN)
            print("\nTechnical details (useful for fixing the script):")
            traceback.print_exc(file=sys.stdout)

        # Work out the verdict
        if failure is not None:
            status = failure.status
            outcome = f"at step {failed_step} ({STEP_NAMES[failed_step]}) - {failure.detail}"
        elif newest is None:
            status = OK
            if counts["reports_found"] == 0:
                outcome = f"- 0 reports found in the last {DAYS_BACK} days, so nothing to parse"
            else:
                outcome = (f"- {counts['reports_found']} reports found, none of them "
                           "electronic, so no transactions to parse")
            advice = ["Access works, but there was no electronic report to read.",
                      "Run it again in a few days to also test reading a report."]
        else:
            status = OK
            outcome = (f"- {counts['reports_found']} reports found, "
                       f"{counts['transactions_parsed']} transactions parsed")

    verdict_line = f"VERDICT: {status} {outcome} (ran on {ran_on})"
    save_json("summary.json", {
        "verdict": status,
        "verdict_line": verdict_line,
        "ran_on": ran_on,
        "github_actions": ran_on == "GitHub Actions",
        "started_at_utc": started.isoformat(timespec="seconds"),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "search_window": {"received_from": f"{date_from:%m/%d/%Y}",
                          "received_to": f"{today:%m/%d/%Y}"},
        "steps": log.steps,
        "counts": counts,
        "akamai_reference": failure.reference if failure else None,
        "script": f"{PROJECT} {VERSION}",
        "python": sys.version.split()[0],
    })

    print()
    advice = advice or ADVICE.get((status, ran_on)) or ADVICE.get(status)
    if advice:
        print("\n".join(advice))
    saved = [name for name in OUTPUT_FILES if (OUTPUT_DIR / name).exists()]
    print(f"Saved in the output folder: {', '.join(saved)}")
    print(verdict_line)
    return 0 if status == OK else 1


if __name__ == "__main__":
    sys.exit(main())
