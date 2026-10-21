"""
parsing.py - turns eFD search results and report pages into clean data.

There is no network or database code in here, so this part is easy to test
on its own. Every value is also kept exactly as written on the page.
"""

import html
import re
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

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


class ReportProblem(Exception):
    """A report page could not be read. The report gets marked as failed."""


def clean(text):
    """Turn runs of spaces and line breaks into single spaces."""
    return " ".join(str(text).split())


def text_of(value):
    """Plain text of a value that may contain HTML tags or HTML codes."""
    value = str(value)
    if "<" in value:
        value = BeautifulSoup(value, "html.parser").get_text(" ")
    return clean(html.unescape(value))


def empty_to_none(text):
    """The site writes "--" for an empty cell; store that as an empty value (NULL)."""
    return None if text in ("", "--") else text


def parse_date(text):
    """MM/DD/YYYY -> a date, or None if there is no valid date in the text."""
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text or "")
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_amount(text):
    """An amount range as written, e.g. "$1,001 - $15,000" -> (1001, 15000).

    "Over $50,000,000" -> (50000001, None): no upper limit.
    """
    text = text or ""
    numbers = [int(number.replace(",", "")) for number in re.findall(r"\d[\d,]*", text)]
    if not numbers:
        return None, None
    if "over" in text.lower():
        return numbers[-1] + 1, None
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if text.strip().endswith("+"):
        return numbers[0], None
    return numbers[0], numbers[0]


def trade_type(text):
    """A short, fixed name for the transaction type, e.g. "Sale (Partial)" -> "sale_partial"."""
    lower = (text or "").lower()
    if not lower or lower == "--":
        return None
    if "purchase" in lower:
        return "purchase"
    if "sale" in lower and "partial" in lower:
        return "sale_partial"
    if "sale" in lower and "full" in lower:
        return "sale_full"
    if "sale" in lower:
        return "sale"
    if "exchange" in lower:
        return "exchange"
    return "other"


def parse_search_row(row, base_url):
    """Turn one search result row into a dict, or None if it looks different.

    Each row is a list: first name, last name, filer (e.g. "Fetterman, John (Senator)"),
    report link (HTML) and date received (MM/DD/YYYY).
    """
    if not isinstance(row, list) or len(row) < 5:
        return None
    link = BeautifulSoup(str(row[3]), "html.parser").find("a", href=True)
    if link is None:
        return None
    url = urljoin(base_url, str(link["href"]))
    path = urlparse(url).path
    if "/search/view/ptr/" in path:
        kind = "electronic"     # a web page with a transactions table
    elif "/search/view/paper/" in path:
        kind = "paper"          # scanned images, read in a later step
    else:
        kind = "other"
    parts = [part for part in path.split("/") if part]
    if not parts:
        return None

    filer = text_of(row[2])
    match = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", filer)
    filer_name, filer_type = (match.group(1), match.group(2)) if match else (filer, None)
    title = clean(link.get_text(" "))
    return {
        "report_path": path,
        "report_uuid": parts[-1],
        "kind": kind,
        "first_name": text_of(row[0]),
        "last_name": text_of(row[1]),
        "filer_name": filer_name,
        "filer_type": filer_type,
        "title": title,
        "is_amendment": "amendment" in title.lower(),
        "date_received": parse_date(text_of(row[4])),
        "url": url,
    }


def parse_report_page(page_html):
    """Read the transactions table of an electronic report.

    Returns (headers, transactions, odd_rows): the column headers as written,
    one dict per transaction, and how many rows had to be skipped because they
    had a different number of cells. Raises ReportProblem if nothing can be read.
    """
    page = BeautifulSoup(page_html, "html.parser")
    for table in page.find_all("table"):
        header_row = table.find("tr")
        if header_row is None:
            continue
        headers = [clean(cell.get_text(" ")) for cell in header_row.find_all(["th", "td"])]
        fields = [COLUMN_FIELDS.get(header.lower()) for header in headers]
        if {"transaction_date", "ticker", "amount"} <= set(fields):
            break
    else:
        raise ReportProblem("no transactions table (with Transaction Date, Ticker and Amount "
                            "columns) on the report page")

    transactions, odd_rows = [], 0
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        if len(cells) != len(headers):
            odd_rows += 1
            continue
        texts = [clean(cell.get_text(" ")) for cell in cells]
        values = {field: text for field, text in zip(fields, texts) if field}
        amount_min, amount_max = parse_amount(values.get("amount"))
        transactions.append({
            "row_number": len(transactions) + 1,
            "transaction_date": parse_date(values.get("transaction_date")),
            "owner": empty_to_none(values.get("owner", "")),
            "ticker": empty_to_none(values.get("ticker", "")),
            "asset_name": empty_to_none(values.get("asset_name", "")),
            "asset_type": empty_to_none(values.get("asset_type", "")),
            "transaction_type": empty_to_none(values.get("transaction_type", "")),
            "trade_type": trade_type(values.get("transaction_type")),
            "amount": empty_to_none(values.get("amount", "")),
            "amount_min": amount_min,
            "amount_max": amount_max,
            "comment": empty_to_none(values.get("comment", "")),
            "raw": dict(zip(headers, texts)),     # every cell exactly as written
        })
    if not transactions:
        raise ReportProblem("the transactions table has no rows that could be read")
    return headers, transactions, odd_rows
