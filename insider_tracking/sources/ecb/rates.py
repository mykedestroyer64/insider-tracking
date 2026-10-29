"""
rates.py - the European Central Bank's euro reference exchange rates.

The ECB publishes, every working day around 16:00 Central European time, the
value of one euro in about 30 currencies ("1 EUR = 11.13 SEK"). The site uses
them to show a euro figure next to trades made in kronor, dollars and so on.
The ECB allows free use of the rates when it is named as the source.

Two files: the last 90 days as XML (small, read on normal runs) and the whole
history since 1999 as a ZIP of one CSV (read once, to fill the table).
"""

import csv
import io
import re
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree

CURRENCIES = ("SEK", "USD", "GBP", "DKK", "NOK", "CHF", "PLN", "CZK", "HUF", "ISK", "CAD", "AUD", "JPY")


class RatesProblem(Exception):
    """A rates file did not look the way this module expects."""


def parse_xml(text):
    """The ECB's XML: [(day, currency, rate), ...] for the currencies we keep."""
    try:
        root = ElementTree.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ElementTree.ParseError as error:
        raise RatesProblem(f"the rates XML could not be read: {error}") from None
    rows = []
    for day_node in root.iter():
        day_text = day_node.attrib.get("time")
        if not day_text:
            continue
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            continue
        for rate_node in day_node:
            currency = rate_node.attrib.get("currency", "").upper()
            rate = rate_node.attrib.get("rate")
            if currency in CURRENCIES and rate:
                try:
                    rows.append((day, currency, float(rate)))
                except ValueError:
                    pass
    if not rows:
        raise RatesProblem("no rates found in the XML")
    return rows


def parse_history_zip(content):
    """The ECB's history ZIP (one CSV: Date,USD,JPY,...): [(day, currency, rate), ...]."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise RatesProblem("no CSV file inside the rates ZIP")
            text = archive.read(names[0]).decode("utf-8-sig")
    except zipfile.BadZipFile:
        raise RatesProblem("the rates history is not a ZIP file") from None
    reader = csv.reader(io.StringIO(text))
    header = [h.strip().upper() for h in next(reader, [])]
    if not header or header[0] != "DATE":
        raise RatesProblem(f"unexpected first column in the rates CSV: {header[:3]}")
    wanted = {i: name for i, name in enumerate(header) if name in CURRENCIES}
    rows = []
    for values in reader:
        if not values or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", values[0].strip()):
            continue
        day = date.fromisoformat(values[0].strip())
        for index, currency in wanted.items():
            if index < len(values):
                raw = values[index].strip()
                if raw and raw.upper() != "N/A":
                    try:
                        rows.append((day, currency, float(raw)))
                    except ValueError:
                        pass
    if not rows:
        raise RatesProblem("no rates found in the history CSV")
    return rows


class Rates:
    """Looks up a rate, falling back to the last published day before (weekends, holidays)."""

    def __init__(self, rows):
        self.by_currency = {}
        for day, currency, rate in rows:
            self.by_currency.setdefault(currency, {})[day] = rate
        self.days = {currency: sorted(days) for currency, days in self.by_currency.items()}

    def latest_day(self):
        return max((days[-1] for days in self.days.values() if days), default=None)

    def rate_on(self, day, currency, lookback=10):
        """Units of `currency` per euro on `day` (or the nearest earlier day), or None."""
        if currency == "EUR":
            return 1.0
        table = self.by_currency.get(currency)
        if not table or day is None:
            return None
        for back in range(lookback + 1):
            rate = table.get(day - timedelta(days=back))
            if rate:
                return rate
        return None
