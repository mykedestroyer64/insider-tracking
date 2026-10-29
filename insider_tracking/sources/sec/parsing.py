"""
parsing.py - turns SEC Form 4 data into clean records of insiders' buys and sells.

Company insiders (officers, directors, 10% owners) report their trades on
Form 4 within two business days. The SEC publishes the same data twice:
  - quarterly "Insider Transactions Data Sets": a ZIP of tab-separated tables
    (SUBMISSION, REPORTINGOWNER, NONDERIV_TRANS, ...) for every Form 3, 4 and 5
  - each filing on EDGAR, as a .txt file with the form's XML inside

Both are read here into the same two kinds of records:
  filing  one Form 4 (or amendment 4/A): the company, the insiders who filed it
  trade   one buy (code P) or sell (code S) of the company's stock from that filing

The site keeps officers' and directors' trades only (10% owners are often funds),
Form 4 and 4/A only (Form 3 lists holdings, Form 5 is a yearly catch-up), and only
open-market or private buys and sells: not awards, option exercises, gifts or tax
withholding. A few filings carry impossible numbers (a price of $1 million a share,
a trade worth more than the company); those rows are kept but marked "suspect" and
left out of totals.

There is no network or database code in here, so this part is easy to test.
"""

import csv
import io
import re
import zipfile
from datetime import date, datetime
from xml.etree import ElementTree

from .names import accession_url, clean_title, company_display_name, display_name, is_entity

KEPT_FORMS = ("4", "4/A")
KEPT_CODES = {"P": "buy", "S": "sell"}
ROLE_WORDS = {"officer": "Officer", "director": "Director", "tenpercentowner": "10% owner", "other": "Other"}

# Sanity limits: anything beyond these is a typo in the filing, not a trade
MAX_PRICE = 1_000_000         # dollars a share (Berkshire Hathaway's A shares trade around $700,000)
MAX_SHARES = 2_000_000_000    # shares in one trade
MAX_VALUE = 20_000_000_000    # dollars in one trade

MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


class FilingProblem(Exception):
    """A filing could not be read."""


# ------------------------------------------------------------------- helpers

def parse_date(text):
    """'13-JUL-2026', '2026-07-13' or '20260713' -> a date (None if unreadable)."""
    text = (text or "").strip()
    match = re.fullmatch(r"(\d{1,2})-([A-Z]{3})-(\d{4})", text.upper())
    if match and match.group(2) in MONTHS:
        try:
            return date(int(match.group(3)), MONTHS[match.group(2)], int(match.group(1)))
        except ValueError:
            return None
    for pattern in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10] if pattern == "%Y-%m-%d" else text, pattern).date()
        except ValueError:
            continue
    return None


def number(text):
    try:
        value = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value == value else None   # not NaN


def flag(text):
    return str(text or "").strip().lower() in ("1", "true", "yes")


def roles_from_flags(director, officer, ten_percent, other):
    roles = []
    if officer:
        roles.append("Officer")
    if director:
        roles.append("Director")
    if ten_percent:
        roles.append("10% owner")
    if other:
        roles.append("Other")
    return roles


def roles_from_text(text):
    """'Director,Officer' (the quarterly tables) -> ['Officer', 'Director']."""
    words = [w.strip().lower() for w in re.split(r"[,;/ ]+", text or "") if w.strip()]
    return roles_from_flags("director" in words, "officer" in words, "tenpercentowner" in words, "other" in words)


def sanity(shares, price):
    """Returns (value, suspect, reason)."""
    if shares is None or shares <= 0:
        return None, True, "no share count"
    if price is None:
        return None, False, None           # price not given: a private transaction, allowed
    if price < 0:
        return None, True, "negative price"
    value = shares * price
    if price > MAX_PRICE:
        return value, True, f"price above ${MAX_PRICE:,} a share"
    if shares > MAX_SHARES:
        return value, True, f"more than {MAX_SHARES:,} shares"
    if value > MAX_VALUE:
        return value, True, f"worth more than ${MAX_VALUE:,}"
    return value, False, None


# ------------------------------------------------------------ record makers

def make_filing(accession, form, filing_date, period, company_cik, company_name, ticker, plan, owners,
                original_date=None):
    """One filing record. `owners` is a list of dicts: cik, name, title, roles (list)."""
    kept = [o for o in owners if ("Officer" in o["roles"] or "Director" in o["roles"]) and not is_entity(o["name"])]
    return {
        "accession": accession,
        "form": form,
        "filing_date": filing_date,
        "period": period,
        "company_cik": (company_cik or "").lstrip("0") or None,
        "company_name": company_display_name(company_name),
        "ticker": normalise_ticker(ticker),
        "plan_10b5_1": plan,
        "is_amendment": form.endswith("/A"),
        "original_date": original_date,
        "owners": owners,
        "kept_owners": kept,
        "url": accession_url(accession, company_cik),
    }


def normalise_ticker(ticker):
    ticker = (ticker or "").strip().upper()
    if not ticker or ticker in ("NONE", "N/A", "NA", "--", "NULL", "NO TICKER", "NOT APPLICABLE"):
        return None
    ticker = ticker.split()[0].strip(".,;")
    return ticker if re.fullmatch(r"[A-Z0-9.\-]{1,10}", ticker) else None


def make_trade(filing, row_key, code, trade_date, shares, price, acquired_disposed, shares_after,
               direct_indirect, nature, security):
    """One buy or sell for the filing's first officer/director (None if it isn't one we keep)."""
    if code not in KEPT_CODES or not filing["kept_owners"]:
        return None
    owner = filing["kept_owners"][0]
    value, suspect, reason = sanity(shares, price)
    return {
        "accession": filing["accession"],
        "row_key": str(row_key),
        "insider_cik": owner["cik"].lstrip("0") or None,
        "insider_name": owner["name"],
        "insider_title": owner["title"],
        "roles": owner["roles"],
        "trade_date": trade_date,
        "code": code,
        "kind": KEPT_CODES[code],
        "shares": shares,
        "price": price,
        "value": value,
        "acquired_disposed": acquired_disposed,
        "shares_after": shares_after,
        "direct": (direct_indirect or "D").upper().startswith("D"),
        "nature": " ".join((nature or "").split()) or None,
        "security": " ".join((security or "").split()) or None,
        "suspect": suspect,
        "suspect_reason": reason,
    }


# ---------------------------------------------------------- quarterly files

def read_table(archive, wanted):
    """Rows of one tab-separated table in the quarterly ZIP, as dicts keyed by the header."""
    names = [n for n in archive.namelist() if n.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper() == wanted]
    if not names:
        raise FilingProblem(f"no {wanted} table in the quarterly file (it has: {', '.join(archive.namelist())})")
    with archive.open(names[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = [column.strip().upper() for column in next(reader)]
        if "ACCESSION_NUMBER" not in header:
            raise FilingProblem(f"the {wanted} table has no ACCESSION_NUMBER column (first row: {header[:6]})")
        for row in reader:
            yield dict(zip(header, row))


def parse_quarter(zip_path):
    """Read one quarterly ZIP. Returns (filings, trades, counts).

    Only Form 4 and 4/A filings with at least one officer or director are
    returned, and only their buys and sells; `counts` says what was skipped.
    """
    counts = {"submissions": 0, "form4": 0, "kept_filings": 0, "rows": 0, "buys_sells": 0,
              "kept_trades": 0, "suspect": 0}
    with zipfile.ZipFile(zip_path) as archive:
        owners = {}
        for row in read_table(archive, "REPORTINGOWNER"):
            owners.setdefault(row["ACCESSION_NUMBER"], []).append({
                "cik": row.get("RPTOWNERCIK", "").strip(),
                "name": display_name(row.get("RPTOWNERNAME", "")),
                "title": clean_title(row.get("RPTOWNER_TITLE", "")),
                "roles": roles_from_text(row.get("RPTOWNER_RELATIONSHIP", "")),
            })
        filings = {}
        for row in read_table(archive, "SUBMISSION"):
            counts["submissions"] += 1
            form = row.get("DOCUMENT_TYPE", "").strip()
            if form not in KEPT_FORMS:
                continue
            counts["form4"] += 1
            filing = make_filing(
                row["ACCESSION_NUMBER"], form, parse_date(row.get("FILING_DATE")),
                parse_date(row.get("PERIOD_OF_REPORT")), row.get("ISSUERCIK"), row.get("ISSUERNAME"),
                row.get("ISSUERTRADINGSYMBOL"), plan_flag(row.get("AFF10B5ONE")),
                owners.get(row["ACCESSION_NUMBER"], []), parse_date(row.get("DATE_OF_ORIG_SUB")))
            if filing["kept_owners"]:
                filings[filing["accession"]] = filing
        trades = []
        for row in read_table(archive, "NONDERIV_TRANS"):
            counts["rows"] += 1
            code = row.get("TRANS_CODE", "").strip()
            if code not in KEPT_CODES:
                continue
            counts["buys_sells"] += 1
            filing = filings.get(row["ACCESSION_NUMBER"])
            if filing is None:
                continue
            trade = make_trade(filing, row.get("NONDERIV_TRANS_SK", ""), code, parse_date(row.get("TRANS_DATE")),
                               number(row.get("TRANS_SHARES")), number(row.get("TRANS_PRICEPERSHARE")),
                               (row.get("TRANS_ACQUIRED_DISP_CD") or "").strip() or None,
                               number(row.get("SHRS_OWND_FOLWNG_TRANS")),
                               row.get("DIRECT_INDIRECT_OWNERSHIP"), row.get("NATURE_OF_OWNERSHIP"),
                               row.get("SECURITY_TITLE"))
            if trade:
                trades.append(trade)
                counts["kept_trades"] += 1
                counts["suspect"] += trade["suspect"]
    with_trades = {t["accession"] for t in trades}
    kept = [f for f in filings.values() if f["accession"] in with_trades]
    counts["kept_filings"] = len(kept)
    return kept, trades, counts


def plan_flag(text):
    """The 10b5-1 checkbox: '1'/'true' -> True, '0'/'false' -> False, missing -> None."""
    text = str(text or "").strip().lower()
    if text in ("1", "true", "yes"):
        return True
    if text in ("0", "false", "no"):
        return False
    return None


# ------------------------------------------------------------- daily index

def parse_daily_index(text):
    """Form 4 filings in a daily form index (form.YYYYMMDD.idx): one per accession number."""
    filings, started, seen = [], False, set()
    for line in text.splitlines():
        if line.startswith("-----"):
            started = True
            continue
        if not started or not line.strip():
            continue
        match = re.match(r"^(\S+(?: \S+)*?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(edgar/data/\S+)\s*$", line)
        if not match:
            continue
        form, company, cik, filed, path = match.groups()
        if form.strip() not in KEPT_FORMS:
            continue
        accession = path.rsplit("/", 1)[-1].replace(".txt", "")
        if accession in seen:
            continue
        seen.add(accession)
        filings.append({"form": form.strip(), "company": company.strip(), "cik": cik,
                        "filed": parse_date(filed), "path": path, "accession": accession})
    return filings


# ---------------------------------------------------------------- one filing

def strip_namespaces(element):
    for node in element.iter():
        if isinstance(node.tag, str) and "}" in node.tag:
            node.tag = node.tag.split("}", 1)[1]
    return element


def parse_filing(text, accession, filing_date):
    """Read one Form 4 submission (.txt with the XML inside). Returns (filing, trades).

    Raises FilingProblem if the XML can't be read.
    """
    match = re.search(r"<XML>\s*(.*?)\s*</XML>", text, re.S | re.I)
    if not match:
        raise FilingProblem("no <XML> part in the filing")
    try:
        doc = strip_namespaces(ElementTree.fromstring(match.group(1).strip().encode("utf-8")))
    except ElementTree.ParseError as error:
        raise FilingProblem(f"the filing's XML is not valid ({error})") from None

    def value(node, path):
        found = node.find(path)
        if found is None:
            return None
        inner = found.find("value")
        return ((inner.text if inner is not None else found.text) or "").strip() or None

    owners = []
    for owner in doc.findall("reportingOwner"):
        rel = owner.find("reportingOwnerRelationship")
        get = (lambda tag: (rel.findtext(tag) or "").strip()) if rel is not None else (lambda tag: "")
        owners.append({
            "cik": (owner.findtext("reportingOwnerId/rptOwnerCik") or "").strip(),
            "name": display_name(owner.findtext("reportingOwnerId/rptOwnerName") or ""),
            "title": clean_title(get("officerTitle")),
            "roles": roles_from_flags(flag(get("isDirector")), flag(get("isOfficer")),
                                      flag(get("isTenPercentOwner")), flag(get("isOther"))),
        })
    form = (doc.findtext("documentType") or "").strip()
    filing = make_filing(accession, form, filing_date, parse_date(doc.findtext("periodOfReport")),
                         doc.findtext("issuer/issuerCik"), doc.findtext("issuer/issuerName"),
                         doc.findtext("issuer/issuerTradingSymbol"), plan_flag(doc.findtext("aff10b5One")),
                         owners)
    trades = []
    for n, t in enumerate(doc.findall("nonDerivativeTable/nonDerivativeTransaction"), 1):
        trade = make_trade(filing, n, (t.findtext("transactionCoding/transactionCode") or "").strip(),
                           parse_date(value(t, "transactionDate")),
                           number(value(t, "transactionAmounts/transactionShares")),
                           number(value(t, "transactionAmounts/transactionPricePerShare")),
                           value(t, "transactionAmounts/transactionAcquiredDisposedCode"),
                           number(value(t, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
                           value(t, "ownershipNature/directOrIndirectOwnership"),
                           value(t, "ownershipNature/natureOfOwnership"),
                           value(t, "securityTitle"))
        if trade:
            trades.append(trade)
    return filing, trades
