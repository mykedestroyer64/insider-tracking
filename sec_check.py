"""
sec_check.py - access test for company insiders' trades (SEC Form 4).

Company insiders (officers, directors and anyone owning more than 10% of a
company) must report their trades in that company's stock to the SEC within
two business days, on Form 4. The SEC publishes them in two ways, and this
script tries both:

  1. open the SEC's "Insider Transactions Data Sets" page and find the newest quarterly file
  2. download it (a ZIP of tables built from every Form 3, 4 and 5 of that quarter)
  3. count the buys and sells in it and save examples
  4. find the newest daily list of filings on EDGAR, the SEC's filing system
  5. read a few of that day's Form 4 filings (XML documents)

Each step gets one status, as in efd_check.py: OK, BLOCKED, SITE_DOWN or
CHANGED (NOT_RUN for steps that never ran). The SEC allows scripts if they
declare who they are and send fewer than 10 requests a second; this one sends
about a dozen requests in all, 2-3 seconds apart, with a User-Agent that names
the project and gives a contact email.

Run it (with your own email):
    python sec_check.py --email you@example.com
On GitHub Actions the email comes from the EFD_CONTACT_EMAIL secret instead.

Results are saved in the "output/sec" folder (the big quarterly file is not
kept). The last line printed is the verdict. Exit code: 0 if OK, 1 otherwise.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
import traceback
import zipfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree

import efd_check   # the polite web session and the status checks are shared with the Senate test
from efd_check import CHANGED, NOT_RUN, OK, StepFailed, check_response, http_codes, short_error


# ------------------------------------------------------------------ settings

PROJECT = "insider-tracking"
VERSION = "0.1"

SEC_URL = "https://www.sec.gov"
DATASETS_PAGE = SEC_URL + "/dera/data/form-345"
DAILY_INDEX = SEC_URL + "/Archives/edgar/daily-index/{year}/QTR{quarter}/index.json"
ARCHIVES = SEC_URL + "/Archives/"

FORM4_SAMPLES = 6         # Form 4 filings to read from the daily list
SAMPLE_ROWS = 300         # buys and sells to save as examples
BYTES_PER_ROW = 300       # rough database size of one saved trade, with its indexes

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "sec"

STEP_NAMES = {
    1: "Find the newest quarterly data file",
    2: "Download it",
    3: "Count the buys and sells in it",
    4: "Find the newest daily list of filings",
    5: "Read a few new Form 4 filings",
}
TOTAL_STEPS = len(STEP_NAMES)

TRANS_CODES = {   # the transaction codes that matter most (the full list is on Form 4's instructions)
    "P": "open-market or private purchase", "S": "open-market or private sale",
    "A": "grant or award", "M": "exercise of a derivative", "F": "tax withholding",
    "G": "gift", "D": "disposition to the issuer", "C": "conversion", "X": "exercise (in the money)",
}


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


def to_number(text):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def get_page(web, url, what):
    """GET a page or file and stop with the right status if it is a block or an error."""
    response = web.request("GET", url)
    check_response(response)
    lower = response.text[:3000].lower() if "text" in response.headers.get("Content-Type", "") else ""
    if "undeclared automated tool" in lower or "request rate threshold exceeded" in lower:
        raise StepFailed(efd_check.BLOCKED, f"the SEC refused {what} (its page about automated tools)",
                         response)
    return response


# --------------------------------------------------------------------- steps

def step1_find_dataset(web):
    response = get_page(web, DATASETS_PAGE, "the data sets page")
    links = {}
    for href in re.findall(r'href="([^"]*?(\d{4})q([1-4])_form345\.zip)"', response.text, re.I):
        url, year, quarter = href
        links[(int(year), int(quarter))] = urljoin(response.url, url)
    if not links:
        raise efd_check.unexpected(response, "no links to quarterly ZIP files (..._form345.zip) on the page")
    newest = max(links)
    return newest, links[newest], len(links), http_codes(response)


def step2_download(web, url, folder):
    response = get_page(web, url, "the quarterly file")
    if not response.content.startswith(b"PK"):
        raise efd_check.unexpected(response, "the quarterly file is not a ZIP file")
    path = Path(folder) / Path(url).name
    path.write_bytes(response.content)
    return path, len(response.content), http_codes(response)


def read_table(archive, wanted):
    """Rows of one tab-separated table in the ZIP, as dicts keyed by the header row."""
    names = [n for n in archive.namelist() if Path(n).stem.upper() == wanted]
    if not names:
        raise StepFailed(CHANGED, f"no {wanted} table in the quarterly file (it has: "
                                  f"{', '.join(archive.namelist())})")
    with archive.open(names[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = [column.strip().upper() for column in next(reader)]
        if "ACCESSION_NUMBER" not in header:
            raise StepFailed(CHANGED, f"the {wanted} table has no ACCESSION_NUMBER column "
                                      f"(first row: {header[:6]})")
        for row in reader:
            yield dict(zip(header, row))


def step3_count(path):
    """Count what the quarter holds, keep examples of buys and sells."""
    with zipfile.ZipFile(path) as archive:
        contents = [{"name": i.filename, "bytes": i.file_size} for i in archive.infolist()]
        headers = {}

        submissions, document_types = {}, Counter()
        for row in read_table(archive, "SUBMISSION"):
            headers.setdefault("SUBMISSION", list(row))
            document_types[row.get("DOCUMENT_TYPE", "")] += 1
            submissions[row["ACCESSION_NUMBER"]] = row

        owners = {}
        for row in read_table(archive, "REPORTINGOWNER"):
            headers.setdefault("REPORTINGOWNER", list(row))
            owners.setdefault(row["ACCESSION_NUMBER"], []).append(row)

        codes, buys_sells, issuers, people = Counter(), [], set(), set()
        value = {"P": 0.0, "S": 0.0}
        missing_price = 0
        date_examples = []
        for row in read_table(archive, "NONDERIV_TRANS"):
            headers.setdefault("NONDERIV_TRANS", list(row))
            code = row.get("TRANS_CODE", "").strip()
            codes[code] += 1
            if code not in ("P", "S"):
                continue
            submission = submissions.get(row["ACCESSION_NUMBER"], {})
            if not submission.get("DOCUMENT_TYPE", "").startswith("4"):
                continue   # keep Form 4 (and 4/A) only
            shares, price = to_number(row.get("TRANS_SHARES")), to_number(row.get("TRANS_PRICEPERSHARE"))
            if shares is not None and price:
                value[code] += shares * price
            else:
                missing_price += 1
            issuers.add(submission.get("ISSUERCIK"))
            for owner in owners.get(row["ACCESSION_NUMBER"], []):
                people.add(owner.get("RPTOWNERCIK"))
            if len(date_examples) < 5:
                date_examples.append(row.get("TRANS_DATE"))
            if len(buys_sells) < SAMPLE_ROWS:
                first_owner = (owners.get(row["ACCESSION_NUMBER"]) or [{}])[0]
                buys_sells.append({
                    "filed": submission.get("FILING_DATE"), "form": submission.get("DOCUMENT_TYPE"),
                    "company": submission.get("ISSUERNAME"), "ticker": submission.get("ISSUERTRADINGSYMBOL"),
                    "insider": first_owner.get("RPTOWNERNAME"),
                    "relationship": first_owner.get("RPTOWNER_RELATIONSHIP"),
                    "title": first_owner.get("RPTOWNER_TITLE"),
                    "traded": row.get("TRANS_DATE"), "code": code,
                    "shares": row.get("TRANS_SHARES"), "price": row.get("TRANS_PRICEPERSHARE"),
                    "acquired_or_disposed": row.get("TRANS_ACQUIRED_DISP_CD"),
                    "shares_after": row.get("SHRS_OWND_FOLWNG_TRANS"),
                    "direct_or_indirect": row.get("DIRECT_INDIRECT_OWNERSHIP"),
                    "plan_10b5_1": submission.get("AFF10B5ONE"),
                    "security": row.get("SECURITY_TITLE"),
                    "accession": row["ACCESSION_NUMBER"],
                })

    buy_sell_rows = codes.get("P", 0) + codes.get("S", 0)
    filing_dates = sorted(s.get("FILING_DATE", "") for s in submissions.values() if s.get("FILING_DATE"))
    return {
        "zip_contents": contents,
        "columns": headers,
        "submissions": len(submissions),
        "document_types": dict(document_types.most_common()),
        "filing_dates": {"first": filing_dates[0] if filing_dates else None,
                         "last": filing_dates[-1] if filing_dates else None},
        "transaction_codes": {code: {"rows": n, "meaning": TRANS_CODES.get(code, "")}
                              for code, n in codes.most_common()},
        "buys": codes.get("P", 0),
        "sells": codes.get("S", 0),
        "buys_sells_value_usd": {"buys": round(value["P"]), "sells": round(value["S"])},
        "buys_sells_without_price": missing_price,
        "companies_with_buys_or_sells": len(issuers),
        "insiders_with_buys_or_sells": len(people),
        "trade_date_examples": date_examples,
        "storage_estimate_mb_per_year": round(buy_sell_rows * 4 * BYTES_PER_ROW / 1e6),
    }, buys_sells


def step4_daily_list(web, today):
    """The newest daily form index (form.YYYYMMDD.idx) of this quarter or the one before."""
    quarter = (today.month - 1) // 3 + 1
    tries = [(today.year, quarter)]
    tries.append((today.year, quarter - 1) if quarter > 1 else (today.year - 1, 4))
    codes = []
    for year, q in tries:
        response = get_page(web, DAILY_INDEX.format(year=year, quarter=q), "the daily index")
        codes += http_codes(response)
        try:
            items = response.json()["directory"]["item"]
        except (ValueError, KeyError, TypeError):
            raise efd_check.unexpected(response, "the daily index listing is not the expected JSON") from None
        days = sorted(m.group(1) for m in (re.fullmatch(r"form\.(\d{8})\.idx", i.get("name", ""))
                                           for i in items) if m)
        if days:
            day = days[-1]
            url = DAILY_INDEX.format(year=year, quarter=q).replace("index.json", f"form.{day}.idx")
            listing = get_page(web, url, "the daily list of filings")
            codes += http_codes(listing)
            (OUTPUT_DIR / f"form.{day}.idx").write_text(listing.text, encoding="utf-8")
            return day, listing.text, codes
    raise StepFailed(CHANGED, "no daily lists of filings (form.YYYYMMDD.idx) in this quarter or the last")


def read_daily_list(text):
    """Filings in a daily form index: form type, company, CIK, date, file path."""
    filings, started = [], False
    for line in text.splitlines():
        if line.startswith("-----"):
            started = True
            continue
        if not started or not line.strip():
            continue
        match = re.match(r"^(.+?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(edgar/data/\S+)\s*$", line)
        if match:
            form, company, cik, filed, path = match.groups()
            filings.append({"form": form.strip(), "company": company.strip(), "cik": cik,
                            "filed": filed, "path": path})
    return filings


def strip_namespaces(element):
    for node in element.iter():
        if isinstance(node.tag, str) and "}" in node.tag:
            node.tag = node.tag.split("}", 1)[1]
    return element


def parse_form4(text):
    """Read the XML part of a Form 4 submission (.txt) into a dict."""
    match = re.search(r"<XML>\s*(.*?)\s*</XML>", text, re.S | re.I)
    if not match:
        raise ValueError("no <XML> part in the filing")
    doc = strip_namespaces(ElementTree.fromstring(match.group(1).strip().encode("utf-8")))

    def value(node, path):
        found = node.find(path)
        if found is None:
            return None
        inner = found.find("value")
        return ((inner.text if inner is not None else found.text) or "").strip() or None

    owners = []
    for owner in doc.findall("reportingOwner"):
        relation = owner.find("reportingOwnerRelationship")
        flags = {}
        if relation is not None:
            for flag in ("isDirector", "isOfficer", "isTenPercentOwner", "isOther"):
                flags[flag] = (relation.findtext(flag) or "").strip().lower() in ("1", "true")
        owners.append({"name": (owner.findtext("reportingOwnerId/rptOwnerName") or "").strip(),
                       "cik": (owner.findtext("reportingOwnerId/rptOwnerCik") or "").strip(),
                       "officer_title": (owner.findtext("reportingOwnerRelationship/officerTitle") or "").strip(),
                       **flags})
    trades = []
    for t in doc.findall("nonDerivativeTable/nonDerivativeTransaction"):
        trades.append({
            "security": value(t, "securityTitle"),
            "date": value(t, "transactionDate"),
            "code": (t.findtext("transactionCoding/transactionCode") or "").strip(),
            "shares": value(t, "transactionAmounts/transactionShares"),
            "price": value(t, "transactionAmounts/transactionPricePerShare"),
            "acquired_or_disposed": value(t, "transactionAmounts/transactionAcquiredDisposedCode"),
            "shares_after": value(t, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
            "direct_or_indirect": value(t, "ownershipNature/directOrIndirectOwnership"),
        })
    return {
        "form": (doc.findtext("documentType") or "").strip(),
        "period": (doc.findtext("periodOfReport") or "").strip(),
        "plan_10b5_1": (doc.findtext("aff10b5One") or "").strip() or None,
        "company": (doc.findtext("issuer/issuerName") or "").strip(),
        "company_cik": (doc.findtext("issuer/issuerCik") or "").strip(),
        "ticker": (doc.findtext("issuer/issuerTradingSymbol") or "").strip(),
        "insiders": owners,
        "trades": trades,
        "derivative_trades": len(doc.findall("derivativeTable/derivativeTransaction")),
    }


def step5_read_filings(web, filings):
    """Read the first few Form 4 filings of the day.

    The list names a filing once per filer (the company and each insider), under each
    filer's own folder, so filings are told apart by their file name (the accession number)."""
    paths, seen = [], set()
    for filing in filings:
        name = Path(filing["path"]).name
        if filing["form"] == "4" and name not in seen:
            seen.add(name)
            paths.append(filing["path"])
    if not paths:
        return [], 0, []
    results, codes = [], []
    (OUTPUT_DIR / "form4").mkdir(exist_ok=True)
    for path in paths[:FORM4_SAMPLES]:
        response = get_page(web, ARCHIVES + path, "a Form 4 filing")
        codes += http_codes(response)
        name = Path(path).name
        (OUTPUT_DIR / "form4" / name).write_text(response.text, encoding="utf-8")
        try:
            parsed = parse_form4(response.text)
        except (ValueError, ElementTree.ParseError) as error:
            raise efd_check.unexpected(response, f"could not read Form 4 {name}: {error}") from None
        parsed["file"] = f"form4/{name}"
        parsed["url"] = ARCHIVES + path
        results.append(parsed)
    return results, len(paths), codes


# ---------------------------------------------------------------------- main

ADVICE = {
    "BLOCKED": ["The SEC refused the script. Its rules ask scripts to give a name and contact",
                "email in the User-Agent and to stay under 10 requests a second, which this one",
                "does. Try one more run later; if it is blocked again, stop and share this result."],
    "SITE_DOWN": ["The SEC site had a server error. Try again later."],
    "CHANGED": ["A page or file did not look the way the script expects. Keep the output folder",
                "and share it."],
}


def main():
    parser = argparse.ArgumentParser(description="Check whether this computer can read company "
                                                 "insiders' trades from the SEC.")
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
            list(OUTPUT_DIR.glob("*.idx")) + list(OUTPUT_DIR.glob("form4/*")):
        old.unlink()

    print(f"SEC access check - {PROJECT} {VERSION}")
    print(f"Running on: {ran_on}")

    log = StepLog()
    quarter_info, daily, failure, failed_step = {}, {}, None, None
    email = (args.email or os.environ.get("EFD_CONTACT_EMAIL") or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        print("\nNo valid contact email was given, so nothing was sent to the SEC.")
        status, outcome = NOT_RUN, "- no contact email given"
    else:
        print("Contact email: set (it goes in the User-Agent and is not saved in output)\n")
        web = efd_check.PoliteSession(f"{PROJECT}/{VERSION} (non-commercial public transparency "
                                      f"project; contact: {email})")
        try:
            with tempfile.TemporaryDirectory() as folder:
                log.begin(1)
                (year, quarter), url, how_many, codes = step1_find_dataset(web)
                quarter_info["quarter"] = f"{year} Q{quarter}"
                log.end(OK, f"{how_many} quarterly files listed, newest {year} Q{quarter}", codes)

                log.begin(2)
                path, size, codes = step2_download(web, url, folder)
                log.end(OK, f"{Path(url).name}, {size / 1e6:.1f} MB", codes)

                log.begin(3)
                counts, examples = step3_count(path)
                quarter_info.update(counts)
                save_json("quarter_summary.json", quarter_info)
                if examples:
                    with open(OUTPUT_DIR / "buys_sells_examples.csv", "w", newline="", encoding="utf-8") as file:
                        writer = csv.DictWriter(file, fieldnames=list(examples[0]))
                        writer.writeheader()
                        writer.writerows(examples)
                if not counts["buys"] + counts["sells"]:
                    raise StepFailed(CHANGED, "no buys or sells (codes P and S) in the quarterly file")
                log.end(OK, f"{counts['submissions']:,} filings; {counts['buys']:,} buys and "
                            f"{counts['sells']:,} sells by {counts['insiders_with_buys_or_sells']:,} "
                            f"insiders in {counts['companies_with_buys_or_sells']:,} companies")

            log.begin(4)
            day, listing, codes = step4_daily_list(web, date.today())
            filings = read_daily_list(listing)
            form4 = {Path(f["path"]).name for f in filings if f["form"] == "4"}
            daily.update(day=day, filings=len({Path(f["path"]).name for f in filings}), form4=len(form4))
            if not filings:
                raise StepFailed(CHANGED, f"could not read any line of the daily list form.{day}.idx")
            log.end(OK, f"{day[4:6]}/{day[6:]}/{day[:4]}: {daily['filings']:,} filings, "
                        f"{len(form4):,} of them Form 4", codes)

            log.begin(5)
            parsed, available, codes = step5_read_filings(web, filings)
            save_json("form4_examples.json", parsed)
            trades = sum(len(p["trades"]) for p in parsed)
            buys_sells = sum(1 for p in parsed for t in p["trades"] if t["code"] in ("P", "S"))
            daily.update(read=len(parsed), trades=trades, buys_sells=buys_sells)
            log.end(OK, f"{len(parsed)} read: {trades} stock transactions, {buys_sells} of them "
                        f"buys or sells", codes)

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
            outcome = (f"- {quarter_info['quarter']}: {quarter_info['buys']:,} buys and "
                       f"{quarter_info['sells']:,} sells; {daily['form4']:,} Form 4 filings on "
                       f"{daily['day'][:4]}-{daily['day'][4:6]}-{daily['day'][6:]}, {daily['read']} read")

    verdict_line = f"VERDICT: {status} {outcome} (ran on {ran_on})"
    save_json("summary.json", {
        "verdict": status, "verdict_line": verdict_line, "ran_on": ran_on,
        "started_at_utc": started.isoformat(timespec="seconds"),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": log.steps, "quarter": quarter_info.get("quarter"), "daily": daily,
        "storage_estimate_mb_per_year": quarter_info.get("storage_estimate_mb_per_year"),
        "script": f"{PROJECT} {VERSION}", "python": sys.version.split()[0],
    })
    print()
    advice = ADVICE.get(status)
    if advice:
        print("\n".join(advice))
    print(f"Saved in {shown(OUTPUT_DIR)}: quarter_summary.json, buys_sells_examples.csv, "
          "the daily list, form4/, form4_examples.json, summary.json")
    print(verdict_line)
    return 0 if status == OK else 1


if __name__ == "__main__":
    sys.exit(main())
