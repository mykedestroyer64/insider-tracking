"""
congress.py - rules the Senate's and the House's trade reports share: amounts written as
ranges, US dates, and the short names of the kinds of trade.
"""

import re
from datetime import date


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


def plain_kinds(reports, paper_word="paper", others=True):
    """'3 electronic, 1 paper' (and ', 2 other' for links of another kind, if asked)."""
    kinds = [report["kind"] for report in reports]
    text = f"{kinds.count('electronic')} electronic, {kinds.count('paper')} {paper_word}"
    rest = len(kinds) - kinds.count("electronic") - kinds.count("paper")
    if others and rest:
        text += f", {rest} other"
    return text
