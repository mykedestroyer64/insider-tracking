"""
parsing.py - turns eFD search results and report pages into clean data.

There is no network or database code in here, so this part is easy to test on its own.
Every value is also kept exactly as written on the page.
"""

import html
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..congress import parse_amount, parse_date, trade_type

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
KEY_FIELDS = {"transaction_date", "ticker", "amount"}


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


# ------------------------------------------------------------- search results

def result_link(cell):
    """The report link in a search result's link cell (HTML), or None."""
    return BeautifulSoup(str(cell), "html.parser").find("a", href=True)


def kind_of(path):
    """'electronic' (a web page with a transactions table), 'paper' (scanned images) or 'other'."""
    if "/search/view/ptr/" in path:
        return "electronic"
    if "/search/view/paper/" in path:
        return "paper"
    return "other"


def parse_search_row(row, base_url):
    """Turn one search result row into a report dict, or None if it looks different.

    Each row is a list: first name, last name, filer (e.g. "Fetterman, John (Senator)"),
    report link (HTML) and date received (MM/DD/YYYY).
    """
    if not isinstance(row, list) or len(row) < 5:
        return None
    link = result_link(row[3])
    if link is None:
        return None
    url = urljoin(base_url, str(link["href"]))
    path = urlparse(url).path
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
        "kind": kind_of(path),
        "first_name": text_of(row[0]),
        "last_name": text_of(row[1]),
        "filer_name": filer_name,
        "filer_type": filer_type,
        "title": title,
        "is_amendment": "amendment" in title.lower(),
        "date_received": parse_date(text_of(row[4])),
        "url": url,
    }


# ------------------------------------------------------------- report pages

def transactions_table(page_html):
    """Find the table whose headers include Transaction Date, Ticker and Amount.

    Returns (headers, fields, rows, odd_rows): the column headers as written, our field
    name for each column (None for columns we don't use), each row's cell texts, and how
    many rows had a different number of cells (skipped). None when there is no such table.
    """
    page = BeautifulSoup(page_html, "html.parser")
    for table in page.find_all("table"):
        header_row = table.find("tr")
        if header_row is None:
            continue
        headers = [clean(cell.get_text(" ")) for cell in header_row.find_all(["th", "td"])]
        fields = [COLUMN_FIELDS.get(header.lower()) for header in headers]
        if KEY_FIELDS <= set(fields):
            break
    else:
        return None
    rows, odd_rows = [], 0
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        if len(cells) != len(headers):
            odd_rows += 1
            continue
        rows.append([clean(cell.get_text(" ")) for cell in cells])
    return headers, fields, rows, odd_rows


def parse_report_page(page_html):
    """Read the transactions table of an electronic report.

    Returns (headers, transactions, odd_rows): the column headers as written, one dict per
    transaction, and how many rows had to be skipped because they had a different number of
    cells. Raises ReportProblem if nothing can be read.
    """
    table = transactions_table(page_html)
    if table is None:
        raise ReportProblem("no transactions table (with Transaction Date, Ticker and Amount "
                            "columns) on the report page")
    headers, fields, rows, odd_rows = table
    transactions = []
    for texts in rows:
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
