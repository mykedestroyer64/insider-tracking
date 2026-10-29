"""
rules.py - which insider trades count.

Rules shared with the Congress data: amendments and rows the collector marked "suspect"
(impossible numbers) are shown but not counted. European rows that the register later revised
or cancelled, rows linked to a share programme (not open-market trades), moves of shares
between an insider and a related party, and trades a joint filer reported twice are shown
but not counted either. Each rule below marks the rows it applies to.
"""

from collections import defaultdict
from datetime import date


def combine_lots(rows):
    """One row per filing, insider, direction and trade date: filers often list every broker fill.

    Shares and values are added up; the price becomes the average paid. Rows marked
    suspect, and rows without a price, are kept apart so their numbers aren't mixed in.
    """
    groups, order = {}, []
    for row in rows:
        key = (row.filing.accession, row.insider_cik, row.kind, row.traded, row.direct, row.security.lower(),
               row.suspect, row.price is None, row.status, row.programme, row.currency)
        if key in groups:
            first = groups[key]
            first.lots += 1
            first.shares = (first.shares or 0) + (row.shares or 0)
            if row.value is not None:
                first.value = (first.value or 0) + row.value
            if row.value_eur is not None:
                first.value_eur = (first.value_eur or 0) + row.value_eur
            if first.shares and first.value is not None:
                first.price = first.value / first.shares
            if row.row_key < first.row_key:
                first.row_key = row.row_key
        else:
            groups[key] = row
            order.append(row)
    return order


def mark_duplicates(rows):
    """The same insider, stock, day, direction, shares and price in two filings: count it once.

    Happens when a fund and its partner each file for the same trade. The earlier filing wins."""
    seen = {}
    for row in sorted(rows, key=lambda r: (r.filing.filed or date.max, r.filing.accession)):
        if row.filing.is_amendment or row.suspect or not row.insider_cik or row.country != "US":
            continue
        key = (row.insider_cik, row.ticker, row.traded, row.kind, round(row.shares or 0, 3),
               round(row.price or 0, 2))
        if key in seen and seen[key] != row.filing.accession:
            row.duplicate = True
        else:
            seen.setdefault(key, row.filing.accession)
    return rows


def mark_transfers(rows):
    """A buy and a sell by the same insider of the same lot on the same day are one transfer.

    European filers report a move of shares between themselves and their own company
    (or a relative) as a disposal by one and an acquisition by the other; nothing was
    bought or sold on the market, so neither side is counted."""
    sides = defaultdict(set)
    for row in rows:
        if row.country != "US" and row.status.lower() == "current" and not row.suspect:
            key = (row.insider_cik, row.security, row.traded, round(row.shares or 0, 3), round(row.price or 0, 2))
            sides[key].add(row.kind)
    for row in rows:
        if row.country != "US" and row.status.lower() == "current" and not row.suspect:
            key = (row.insider_cik, row.security, row.traded, round(row.shares or 0, 3), round(row.price or 0, 2))
            if sides[key] == {"buy", "sell"}:
                row.transfer = True
    return rows


def mark_superseded(rows):
    """A European register's corrected filing replaces the original line for the same trade.

    The register marks the original 'Revised' itself, but only within the window the
    collector re-reads; here the older line is marked when a correction exists."""
    corrections = defaultdict(list)
    for row in rows:
        if row.country != "US" and row.filing.is_amendment and row.status.lower() == "current":
            corrections[(row.insider_cik, row.filing.company_cik, row.traded, row.kind)].append(row.filing.filed)
    for row in rows:
        if row.country == "US" or row.filing.is_amendment or row.status.lower() != "current":
            continue
        later = corrections.get((row.insider_cik, row.filing.company_cik, row.traded, row.kind))
        if later and row.filing.filed and any(d and d > row.filing.filed for d in later):
            row.superseded = True
    return rows
