"""
house_parsing.py - reads the House's Periodic Transaction Reports (PDF files).

An electronic House trade report is a PDF with one table: Owner, Asset,
Transaction Type, Date, Notification Date, Amount and "Cap. Gains > $200?".
Each transaction takes a few lines:

    JT  Datadog, Inc. - Class A Common    P   09/28/2026  10/01/2026  $50,001 -
        Stock (DDOG) [ST]                                             $100,000
        Filing Status: New
        Subholding Of: Morgan Stanley Trust Account
        Description: ...

The first line has the owner, the start of the asset's name, the type, both
dates and the start of the amount; the next lines finish the name and the
amount; then come the details. The columns are found from the positions of
the header words on each page, so the small layout changes between years
don't matter. (Some of the PDF's fonts drop letters when read as text:
"Filing Status" comes out as "F S", "CLNE" as "ClNE". Both are handled.)

Scanned (paper) reports have no text, so there is nothing to read in them.
There is no network or database code in here, so this part is easy to test.
"""

import io
import re

import parsing   # shared helpers: amounts, dates, trade types

OWNERS = {"SP": "Spouse", "JT": "Joint", "DC": "Child", "": "Self"}
TYPES = {   # House code -> (the words the Senate uses, our short name)
    "P": ("Purchase", "purchase"),
    "S": ("Sale", "sale"),
    "S (PARTIAL)": ("Sale (Partial)", "sale_partial"),
    "E": ("Exchange", "exchange"),
}
ASSET_TYPES = {   # the House's asset codes (fd.house.gov/reference/asset-type-codes.aspx)
    "ST": "Stock", "OP": "Stock Option", "PS": "Non-Public Stock", "RS": "Restricted Stock Units",
    "SA": "Stock Appreciation Right", "EF": "Exchange Traded Fund", "ET": "Exchange Traded Note",
    "MF": "Mutual Fund", "GS": "Government Security", "CS": "Corporate Bond",
    "AB": "Asset-Backed Security", "RE": "REIT", "CT": "Cryptocurrency", "FU": "Futures",
    "FE": "Currency", "PM": "Precious Metals", "HN": "Private Fund", "HE": "Private Fund",
    "OI": "Ownership Interest", "OL": "Business Interest", "RP": "Real Property",
    "BA": "Bank Account", "IR": "IRA", "4K": "Retirement Account", "5C": "529 Plan",
    "5F": "529 Plan", "5P": "529 Plan", "TR": "Trust", "VA": "Variable Annuity",
    "VI": "Variable Insurance", "WU": "Whole/Universal Insurance", "FN": "Fixed Annuity",
    "OT": "Other",
}

HEADER_WORDS = ("owner", "asset", "transaction", "date", "notification", "amount")
DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
TYPE = re.compile(r"^(P|S|E|S\s*\(partial\))$", re.I)
CODE = re.compile(r"\[([A-Z0-9]{2})\]")
TICKER = re.compile(r"\(([A-Za-z][A-Za-z0-9.\-/]{0,7})\)")
DETAILS = [   # label patterns (full words, or the letters left when a font drops the rest)
    ("filing_status", re.compile(r"^(?:F\s*S|FILING\s+STATUS)\s*:\s*(.*)$", re.I)),
    ("subholding_of", re.compile(r"^(?:S\s*O|SUBHOLDING\s+OF)\s*:\s*(.*)$", re.I)),
    ("description", re.compile(r"^(?:D|DESCRIPTION)\s*:\s*(.*)$", re.I)),
    ("comments", re.compile(r"^(?:C|COMMENTS?)\s*:\s*(.*)$", re.I)),
]
END_OF_TABLE = re.compile(r"^(\* For the complete list|I\s+V\s+D$|I\s+P\s+O$|C\s+S$|"
                          r"Investment Vehicle Details|Initial Public Offerings|"
                          r"Certification and Signature|I CERTIFY|Digitally Signed)", re.I)
PAGE_TOP = re.compile(r"^(Filing ID #|Clerk of the House|Name:|Status:|State/District:|"
                      r"P\s+T\s+R|Periodic Transaction|F\s+I$|Filer Information|T$|Transactions$)", re.I)


class ReportProblem(Exception):
    """A report could not be read. The report gets marked as failed."""


# ------------------------------------------------------------- yearly lists

def parse_list(xml_bytes, report_url):
    """The trade reports in one of the Clerk's yearly lists of filings ({year}FD.xml).

    Each <Member> entry is one filing: Prefix, Last, First, Suffix, FilingType,
    StateDst (state and district, e.g. CA11), Year, FilingDate (M/D/YYYY) and
    DocID. Trade reports have FilingType "P". Electronic reports have 8-digit
    DocIDs; scanned paper reports have shorter ones. `report_url` is a pattern
    with {year} and {doc_id}. Raises ReportProblem if the list can't be read.
    """
    from xml.etree import ElementTree
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as error:
        raise ReportProblem(f"the list is not valid XML ({error})") from None
    members = list(root.iter("Member"))
    if not members:
        raise ReportProblem("the list has no <Member> entries")
    reports = []
    for member in members:
        def field(tag):
            return " ".join((member.findtext(tag) or "").split())
        if field("FilingType") != "P":
            continue
        doc_id, year = field("DocID"), field("Year")
        if not doc_id.isdigit() or not year.isdigit():
            raise ReportProblem(f"an entry has an unexpected DocID or Year ({doc_id!r}, {year!r})")
        last = field("Last") + (f", {field('Suffix')}" if field("Suffix") else "")
        state_district = field("StateDst").upper()
        reports.append({
            "report_path": f"/house/{year}/{doc_id}",
            "report_uuid": doc_id,
            "kind": "electronic" if len(doc_id) >= 8 else "paper",
            "first_name": field("First"),
            "last_name": last,
            "filer_name": " ".join(part for part in (field("Prefix"), field("First"), last) if part),
            "filer_type": "Representative" if re.fullmatch(r"[A-Z]{2}\d{1,2}", state_district) else "Other",
            "title": "Periodic Transaction Report",
            "is_amendment": False,
            "date_received": parsing.parse_date(field("FilingDate")),
            "url": report_url.format(year=year, doc_id=doc_id),
            "chamber": "house",
            "state_district": state_district or None,
        })
    return reports


# ------------------------------------------------------------------- reading

def pdf_lines(pdf_bytes):
    """Every page's words, grouped into lines from top to bottom (with positions)."""
    import pdfplumber   # imported here so this file can be read without it installed
    pages = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                words = []
                for word in page.extract_words(x_tolerance=1.5, y_tolerance=2):
                    # some fonts turn letters into invisible "null" characters:
                    # "Filing Status:" comes out as "F\0\0\0\0\0 S\0\0\0\0\0:"
                    word["text"] = word["text"].replace("\x00", "")
                    if word["text"]:
                        words.append(word)
                lines = []
                for word in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
                    if lines and abs(word["top"] - lines[-1][0]["top"]) <= 2.5:
                        lines[-1].append(word)
                    else:
                        lines.append([word])
                pages.append([sorted(line, key=lambda w: w["x0"]) for line in lines])
    except Exception as error:   # a damaged or unusual PDF
        raise ReportProblem(f"the PDF could not be opened ({type(error).__name__}: {error})") from None
    return pages


def line_text(line):
    return " ".join(word["text"] for word in line)


def find_columns(line):
    """If this line is the table's header, return the left edge of each column."""
    found = {}
    for word in line:
        key = word["text"].lower().strip(".")
        if key in HEADER_WORDS and key not in found:
            found[key] = word["x0"]
        elif key == "cap":
            found["cap"] = word["x0"]
    if not all(name in found for name in HEADER_WORDS):
        return None
    edge = 3   # words start a little left of their header now and then
    columns = [("owner", found["owner"] - edge), ("asset", found["asset"] - edge),
               ("type", found["transaction"] - edge), ("date", found["date"] - edge),
               ("notification", found["notification"] - edge), ("amount", found["amount"] - edge)]
    if "cap" in found:
        columns.append(("cap", found["cap"] - edge))
    return columns


def split_by_column(line, columns):
    """The line's words, column by column ('id' for anything left of the owner column)."""
    cells = {name: [] for name, _ in columns}
    cells["id"] = []
    for word in line:
        name = "id"
        for column, left in columns:
            if word["x0"] >= left:
                name = column
        cells[name].append(word["text"])
    return {name: " ".join(words) for name, words in cells.items()}


def is_header_rest(text):
    """The header's second and third lines: 'Type Date Gains >' and '$200?'."""
    words = text.lower().replace(">", " ").split()
    return bool(words) and all(w in ("type", "date", "gains", "$200?", "cap.") for w in words)


def detail_of(text):
    for name, pattern in DETAILS:
        match = pattern.match(text)
        if match:
            return name, match.group(1).strip()
    return None, None


def parse_report(pdf_bytes):
    """Read a report's transactions. Returns (transactions, expected).

    `expected` is how many transactions the text seems to hold (lines with two
    dates), to check that none were missed. Raises ReportProblem if nothing
    can be read.
    """
    pages = pdf_lines(pdf_bytes)
    if not any(pages):
        raise ReportProblem("the PDF has no text (it is probably a scan)")

    rows, columns, current, in_details, ended = [], None, None, False, False
    last_detail, expected = None, 0
    for page in pages:
        page_columns = None
        for line in page:
            text = line_text(line)
            if re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}/\d{1,2}/\d{4}\b", text):
                expected += 1
            header = find_columns(line)
            if header:
                columns = page_columns = header
                ended = False
                continue
            if ended or columns is None:
                continue
            if page_columns is None and PAGE_TOP.match(text):
                continue   # the top of a new page, before its header
            if END_OF_TABLE.match(text):
                ended = True
                continue
            if is_header_rest(text):
                continue
            cells = split_by_column(line, columns)
            if DATE.match(cells["date"]) and TYPE.match(cells["type"].replace(" ", "")
                                                          .replace("(", " (").strip()):
                current = {"id": cells["id"], "owner": cells["owner"], "asset": cells["asset"],
                           "type": cells["type"], "date": cells["date"],
                           "notification_date": cells["notification"], "amount": cells["amount"],
                           "filing_status": "", "subholding_of": "", "description": "",
                           "comments": ""}
                rows.append(current)
                in_details = False
                last_detail = None
                continue
            if current is None:
                continue
            name, value = detail_of(text)
            if name:
                current[name] = value
                last_detail, in_details = name, True
            elif in_details:
                if last_detail:   # a detail that runs onto the next line
                    current[last_detail] = f"{current[last_detail]} {text}".strip()
            else:                 # the rest of the asset's name, the type or the amount
                for column in ("asset", "type", "amount"):
                    if cells[column]:
                        current[column] = f"{current[column]} {cells[column]}".strip()

    if columns is None:
        raise ReportProblem("no transactions table (no header with Owner, Asset, Transaction, "
                            "Date, Notification and Amount)")
    if not rows:
        raise ReportProblem("the transactions table has no rows that could be read")
    return [transaction(number, row) for number, row in enumerate(rows, 1)], expected


def transaction(number, row):
    """Turn one table row into the same fields the Senate's transactions use."""
    asset = " ".join(row["asset"].split())
    codes = CODE.findall(asset)
    code = codes[-1] if codes else None
    ticker = None
    tickers = TICKER.findall(asset)
    if tickers:
        candidate = tickers[-1].upper()
        if re.fullmatch(r"[A-Z][A-Z0-9.\-/]{0,7}", candidate) and not candidate.isdigit():
            ticker = candidate
    name = CODE.sub("", asset)
    if ticker:
        name = re.sub(r"\(" + re.escape(tickers[-1]) + r"\)", "", name)
    name = " ".join(name.replace(" - ", " - ").split()).strip(" -")

    kind = " ".join(row["type"].upper().replace("(", " (").split())
    transaction_type, short = TYPES.get(kind, (row["type"], "other"))
    amount = " ".join(row["amount"].split())
    amount_min, amount_max = parsing.parse_amount(amount)
    comment = "; ".join(part for part in (row["description"], row["comments"]) if part) or None
    return {
        "row_number": number,
        "transaction_date": parsing.parse_date(row["date"]),
        "owner": OWNERS.get(row["owner"].strip().upper(), row["owner"].strip() or "Self"),
        "ticker": ticker,
        "asset_name": name or None,
        "asset_type": ASSET_TYPES.get(code, code) if code else None,
        "transaction_type": transaction_type,
        "trade_type": short,
        "amount": amount or None,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "comment": comment,
        "raw": {key: value for key, value in row.items() if value},   # every cell as read
    }


def is_amendment(transactions):
    """A report whose transactions are all marked 'Amended' re-files an earlier one."""
    statuses = [t["raw"].get("filing_status", "").lower() for t in transactions]
    return bool(statuses) and all("amend" in status for status in statuses)
