"""
sec.py - can a script read company insiders' trades (SEC Form 4)?

Company insiders (officers, directors and anyone owning more than 10% of a company) must
report their trades in that company's stock to the SEC within two business days, on Form 4.
The SEC publishes them in two ways, and this check tries both:

  1. open the SEC's "Insider Transactions Data Sets" page and find the newest quarterly file
  2. download it (a ZIP of tables built from every Form 3, 4 and 5 of that quarter)
  3. count the buys and sells in it and save examples
  4. find the newest daily list of filings on EDGAR, the SEC's filing system
  5. read a few of that day's Form 4 filings (XML documents)

The SEC allows scripts if they declare who they are and send fewer than 10 requests a second;
this one sends about a dozen requests in all. Results are saved in "output/sec" (the big
quarterly file is not kept).
"""

import csv
import io
import re
import tempfile
import zipfile
from collections import Counter
from dataclasses import replace
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree

from ..core.errors import SiteStop
from ..core.status import BLOCKED, CHANGED, OK
from ..web.checks import BlockPageCheck, unexpected
from ..web.describe import http_codes
from ..sources.sec.parsing import strip_namespaces
from ..sources.sec.site import QUARTERLY_LINK, SecEndpoints
from .framework import PACING, Check, CheckDefinition, CheckRunner, common_summary, script_fields, shown, times, write_json

FORM4_SAMPLES = 6         # Form 4 filings to read from the daily list
SAMPLE_ROWS = 300         # buys and sells to save as examples
BYTES_PER_ROW = 300       # rough database size of one saved trade, with its indexes

TRANS_CODES = {   # the transaction codes that matter most (the full list is on Form 4's instructions)
    "P": "open-market or private purchase", "S": "open-market or private sale",
    "A": "grant or award", "M": "exercise of a derivative", "F": "tax withholding",
    "G": "gift", "D": "disposition to the issuer", "C": "conversion", "X": "exercise (in the money)",
}

DEFINITION = CheckDefinition(
    title="SEC access check",
    description="Check whether this computer can read company insiders' trades from the SEC.",
    steps=("Find the newest quarterly data file", "Download it", "Count the buys and sells in it",
           "Find the newest daily list of filings", "Read a few new Form 4 filings"),
    site="the SEC",
    folder="sec",
    advice={
        "BLOCKED": ["The SEC refused the script. Its rules ask scripts to give a name and contact",
                    "email in the User-Agent and to stay under 10 requests a second, which this one",
                    "does. Try one more run later; if it is blocked again, stop and share this result."],
        "SITE_DOWN": ["The SEC site had a server error. Try again later."],
        "CHANGED": ["A page or file did not look the way the script expects. Keep the output folder",
                    "and share it."],
    },
)


def to_number(text):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def read_table(archive, wanted):
    """Rows of one tab-separated table in the ZIP, as dicts keyed by the header row."""
    names = [n for n in archive.namelist() if Path(n).stem.upper() == wanted]
    if not names:
        raise SiteStop(CHANGED, f"no {wanted} table in the quarterly file (it has: {', '.join(archive.namelist())})")
    with archive.open(names[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = [column.strip().upper() for column in next(reader)]
        if "ACCESSION_NUMBER" not in header:
            raise SiteStop(CHANGED, f"the {wanted} table has no ACCESSION_NUMBER column (first row: {header[:6]})")
        for row in reader:
            yield dict(zip(header, row))


def count_quarter(path):
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


def parse_form4(text):
    """Read the XML part of a Form 4 submission (.txt) into a dict, as filed."""
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


class SecCheck(Check):
    def __init__(self, endpoints=SecEndpoints(), pacing=PACING):
        self.endpoints = endpoints
        self.definition = replace(DEFINITION, pacing=pacing)
        self.check = BlockPageCheck(name_the_page=False)

    def clear(self, folder):
        for old in list(folder.glob("*.json")) + list(folder.glob("*.csv")) + \
                list(folder.glob("*.idx")) + list(folder.glob("form4/*")):
            old.unlink()

    def setup(self, run):
        run.info.update(quarter={}, daily={})

    def get_page(self, client, url, what):
        """GET a page or file and stop with the right status if it is a refusal or an error."""
        response = client.get(url, check=self.check)
        lower = response.text[:3000].lower() if "text" in response.headers.get("Content-Type", "") else ""
        if "undeclared automated tool" in lower or "request rate threshold exceeded" in lower:
            raise SiteStop(BLOCKED, f"the SEC refused {what} (its page about automated tools)", response)
        return response

    # ---------------------------------------------------------------- steps

    def run_steps(self, run, client):
        log, quarter_info, daily = run.log, run.info["quarter"], run.info["daily"]
        with tempfile.TemporaryDirectory() as folder:
            log.begin(1)
            response = self.get_page(client, self.endpoints.datasets_page, "the data sets page")
            links = {}
            for url, year, quarter in QUARTERLY_LINK.findall(response.text):
                links[(int(year), int(quarter))] = urljoin(response.url, url)
            if not links:
                raise unexpected(response, "no links to quarterly ZIP files (..._form345.zip) on the page")
            year, quarter = max(links)
            url = links[(year, quarter)]
            quarter_info["quarter"] = f"{year} Q{quarter}"
            log.end(OK, f"{len(links)} quarterly files listed, newest {year} Q{quarter}", http_codes(response))

            log.begin(2)
            response = self.get_page(client, url, "the quarterly file")
            if not response.content.startswith(b"PK"):
                raise unexpected(response, "the quarterly file is not a ZIP file")
            path = Path(folder) / Path(url).name
            path.write_bytes(response.content)
            log.end(OK, f"{Path(url).name}, {len(response.content) / 1e6:.1f} MB", http_codes(response))

            log.begin(3)
            counts, examples = count_quarter(path)
            quarter_info.update(counts)
            write_json(run.folder / "quarter_summary.json", quarter_info)
            if examples:
                with open(run.folder / "buys_sells_examples.csv", "w", newline="", encoding="utf-8") as file:
                    writer = csv.DictWriter(file, fieldnames=list(examples[0]))
                    writer.writeheader()
                    writer.writerows(examples)
            if not counts["buys"] + counts["sells"]:
                raise SiteStop(CHANGED, "no buys or sells (codes P and S) in the quarterly file")
            log.end(OK, f"{counts['submissions']:,} filings; {counts['buys']:,} buys and "
                        f"{counts['sells']:,} sells by {counts['insiders_with_buys_or_sells']:,} "
                        f"insiders in {counts['companies_with_buys_or_sells']:,} companies")

        log.begin(4)
        day, listing, codes = self.newest_daily_list(run, client)
        filings = read_daily_list(listing)
        form4 = {Path(f["path"]).name for f in filings if f["form"] == "4"}
        daily.update(day=day, filings=len({Path(f["path"]).name for f in filings}), form4=len(form4))
        if not filings:
            raise SiteStop(CHANGED, f"could not read any line of the daily list form.{day}.idx")
        log.end(OK, f"{day[4:6]}/{day[6:]}/{day[:4]}: {daily['filings']:,} filings, "
                    f"{len(form4):,} of them Form 4", codes)

        log.begin(5)
        parsed, codes = self.read_filings(run, client, filings)
        write_json(run.folder / "form4_examples.json", parsed)
        trades = sum(len(p["trades"]) for p in parsed)
        buys_sells = sum(1 for p in parsed for t in p["trades"] if t["code"] in ("P", "S"))
        daily.update(read=len(parsed), trades=trades, buys_sells=buys_sells)
        log.end(OK, f"{len(parsed)} read: {trades} stock transactions, {buys_sells} of them buys or sells", codes)

    def newest_daily_list(self, run, client):
        """The newest daily form index (form.YYYYMMDD.idx) of this quarter or the one before."""
        today = run.today
        quarter = (today.month - 1) // 3 + 1
        tries = [(today.year, quarter), (today.year, quarter - 1) if quarter > 1 else (today.year - 1, 4)]
        codes = []
        for year, q in tries:
            listing_url = self.endpoints.daily_listing.format(year=year, quarter=q)
            response = self.get_page(client, listing_url, "the daily index")
            codes += http_codes(response)
            try:
                items = response.json()["directory"]["item"]
            except (ValueError, KeyError, TypeError):
                raise unexpected(response, "the daily index listing is not the expected JSON") from None
            days = sorted(m.group(1) for m in (re.fullmatch(r"form\.(\d{8})\.idx", i.get("name", ""))
                                               for i in items) if m)
            if days:
                day = days[-1]
                listing = self.get_page(client, listing_url.replace("index.json", f"form.{day}.idx"),
                                        "the daily list of filings")
                codes += http_codes(listing)
                (run.folder / f"form.{day}.idx").write_text(listing.text, encoding="utf-8")
                return day, listing.text, codes
        raise SiteStop(CHANGED, "no daily lists of filings (form.YYYYMMDD.idx) in this quarter or the last")

    def read_filings(self, run, client, filings):
        """Read the first few Form 4 filings of the day.

        The list names a filing once per filer (the company and each insider), under each
        filer's own folder, so filings are told apart by their file name (the accession number)."""
        paths, seen = [], set()
        for filing in filings:
            name = Path(filing["path"]).name
            if filing["form"] == "4" and name not in seen:
                seen.add(name)
                paths.append(filing["path"])
        results, codes = [], []
        if not paths:
            return results, codes
        (run.folder / "form4").mkdir(exist_ok=True)
        for path in paths[:FORM4_SAMPLES]:
            response = self.get_page(client, self.endpoints.archives + path, "a Form 4 filing")
            codes += http_codes(response)
            name = Path(path).name
            (run.folder / "form4" / name).write_text(response.text, encoding="utf-8")
            try:
                parsed = parse_form4(response.text)
            except (ValueError, ElementTree.ParseError) as error:
                raise unexpected(response, f"could not read Form 4 {name}: {error}") from None
            parsed["file"] = f"form4/{name}"
            parsed["url"] = self.endpoints.archives + path
            results.append(parsed)
        return results, codes

    # ------------------------------------------------------------ summing up

    def outcome(self, run):
        quarter_info, daily = run.info["quarter"], run.info["daily"]
        return (f"- {quarter_info['quarter']}: {quarter_info['buys']:,} buys and "
                f"{quarter_info['sells']:,} sells; {daily['form4']:,} Form 4 filings on "
                f"{daily['day'][:4]}-{daily['day'][4:6]}-{daily['day'][6:]}, {daily['read']} read")

    def summary(self, run, verdict_line):
        quarter_info = run.info["quarter"]
        return {**common_summary(run, verdict_line), **times(run), "steps": run.log.steps,
                "quarter": quarter_info.get("quarter"), "daily": run.info["daily"],
                "storage_estimate_mb_per_year": quarter_info.get("storage_estimate_mb_per_year"),
                **script_fields()}

    def saved_line(self, run):
        return (f"Saved in {shown(run.folder)}: quarter_summary.json, buys_sells_examples.csv, "
                "the daily list, form4/, form4_examples.json, summary.json")


def command():
    """`python -m insider_tracking check sec`."""
    return CheckRunner(SecCheck())
