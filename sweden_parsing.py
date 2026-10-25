"""
sweden_parsing.py - reads Finansinspektionen's export of executives' trades (Sweden).

Sweden's financial regulator, Finansinspektionen (FI), keeps the public register
of trades that executives and board members of listed companies ("persons
discharging managerial responsibilities", PDMRs, under Article 19 of the EU's
Market Abuse Regulation) and their close relatives or companies must report
within three business days. The register's search page offers an export: a
UTF-16 text file, one line per transaction, fields separated by semicolons,
with these columns (English version):

  Publication date; Issuer; LEI-code; Notifier; Person discharging managerial
  responsibilities; Position; Closely associated; Amendment; Details of
  amendment; Initial notification; Linked to share option programme; Nature of
  transaction; Intrument type (sic); Instrument name; ISIN; Transaction date;
  Volume; Unit; Price; Currency; Trading venue; Status

What is kept, as in the SEC section of the site: buys ("Acquisition") and sells
("Disposal") of shares and depositary receipts. Subscriptions in new issues,
option exercises, gifts, inheritances and so on are not trades and are left
out. Rows whose status is "Revised" or "Cancelled", rows linked to a share
programme and rows with impossible numbers are kept but marked, so the website
can show them without counting them.

Every row gets a key made from its contents (not its status), so the same row
exported twice is stored once and a status change updates it.
"""

import csv
import hashlib
import io
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

COUNTRY = "SE"
SOURCE = "Finansinspektionen"
STOCKHOLM = ZoneInfo("Europe/Stockholm")

KINDS = {"acquisition": "buy", "disposal": "sell"}
INSTRUMENTS_KEPT = ("share", "depositary receipt", "bta")   # BTA: a paid new share awaiting registration

MAX_PRICE = 1_000_000          # per share, in the trade's currency
MAX_VOLUME = 2_000_000_000
MAX_VALUE_EUR = 5_000_000_000  # a single trade above this is almost certainly a typo in the filing

POSITIONS = {   # the register's position names, shortened for the site
    "member of the board of directors": ("Board member", "Director"),
    "deputy member of the board of directors": ("Deputy board member", "Director"),
    "chairman of the board of directors": ("Chairman", "Director"),
    "vice chairman of the board of directors": ("Vice chairman", "Director"),
    "chief executive officer (ceo)/managing directory": ("CEO", "Officer"),   # "Directory" is the register's spelling
    "chief executive officer (ceo)/managing director": ("CEO", "Officer"),
    "deputy ceo/deputy managing director": ("Deputy CEO", "Officer"),
    "chief financial officer (cfo)": ("CFO", "Officer"),
    "other senior executive": ("Senior executive", "Officer"),
    "other member of the company's administrative, management or supervisory body": ("Other officer", "Officer"),
    "employee representative of the board of directors or deputy employee representative of the board of directors":
        ("Employee representative", "Director"),
    "auditor": ("Auditor", "Other"),
    "deputy auditor": ("Deputy auditor", "Other"),
}

REQUIRED_COLUMNS = ("Publication date", "Issuer", "Person discharging managerial responsibilities", "Position",
                    "Nature of transaction", "ISIN", "Transaction date", "Volume", "Price", "Currency", "Status")

# Words that mean a "person" is really a company, fund or foundation (Swedish and English forms)
ENTITY_WORDS = (r"\b(?:AB|Aktiebolag|AS|ASA|A/S|ApS|Oy|Oyj|KB|HB|Ltd|Limited|LLC|L\.?P\.?|Inc|Corp|GmbH|AG|SE|SA|S\.A\.|"
                r"PLC|Holding|Holdings|Invest|Investment|Investments|Förvaltning|Förvaltnings|Kapital|Capital|Stiftelse|"
                r"Stiftelsen|Foundation|Fund|Fonder|Partners|Trust|Group|Gruppen|Bolag|Bolaget|Fastigheter|Konsult)\b")
ENTITY = re.compile(ENTITY_WORDS, re.I)

COMPANY_SUFFIXES = re.compile(r"\s*,?\s*(?:\(publ\)|\(PUBL\)|AB|Aktiebolag|ASA|AS|Oyj|Oy|A/S|plc|PLC|SE|Ltd|Limited|NV|"
                              r"N\.V\.|SA|S\.A\.|AG|Abp)\s*$")


class ExportProblem(Exception):
    """The export did not look the way this module expects."""


# ------------------------------------------------------------------ helpers

def tidy(text):
    """Collapse the register's non-breaking and repeated spaces."""
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def decode_export(content):
    """The export is UTF-16 text (with or without a byte-order mark); fall back to UTF-8."""
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    if len(content) >= 2 and content[1:2] == b"\x00":
        return content.decode("utf-16-le")
    return content.decode("utf-8-sig")


def to_number(text):
    text = tidy(text).replace(" ", "")
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_day(text):
    """'22/10/2026 00:00:00' or '2026-10-22' -> date."""
    text = tidy(text)
    for pattern in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def parse_published(text):
    """'24/10/2026 21:31:10' (Swedish local time) -> an aware UTC datetime."""
    text = tidy(text)
    for pattern in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            local = datetime.strptime(text, pattern).replace(tzinfo=STOCKHOLM)
            return local.astimezone(timezone.utc)
        except ValueError:
            continue
    day = parse_day(text)
    if day:
        return datetime(day.year, day.month, day.day, tzinfo=STOCKHOLM).astimezone(timezone.utc)
    return None


def yes(text):
    return tidy(text).lower() in ("yes", "ja", "true", "1")


def is_entity(name):
    """True for names that are companies, foundations or funds rather than people."""
    name = tidy(name)
    return bool(ENTITY.search(name)) or name[:1].isdigit()


def person_display_name(name):
    """Register names are mostly normal; fix the ones written in capitals ('ANNA SVENSSON')."""
    name = tidy(name)
    if name and name == name.upper() and any(c.isalpha() for c in name):
        return " ".join("-".join(part.capitalize() for part in word.split("-")) for word in name.split())
    return name


def company_display_name(name):
    """'EASTNINE AB (PUBL)' -> 'Eastnine AB (publ)'; names already in normal case are kept."""
    name = tidy(name)
    if name == name.upper() and any(c.isalpha() for c in name):
        words = []
        for word in name.split():
            low = word.lower()
            if low in ("ab", "asa", "as", "plc", "se", "ag", "sa", "nv", "oyj", "oy", "ltd", "kb", "hb", "ab,"):
                words.append(word.upper() if low not in ("oyj", "oy", "ltd") else word.capitalize())
            elif low in ("(publ)", "(publ)."):
                words.append("(publ)")
            elif len(word) <= 3 and word.isalpha():
                words.append(word)            # ICA, SEB, SKF, AAK, NCC, ...
            else:
                words.append("-".join(p.capitalize() for p in word.split("-")))
        name = " ".join(words)
    return name


def company_short_name(name):
    """The name without company-form suffixes: 'Corem Property Group AB' -> 'Corem Property Group'."""
    name = company_display_name(name)
    short = name
    for _ in range(3):
        trimmed = COMPANY_SUFFIXES.sub("", short)
        if trimmed == short or not trimmed:
            break
        short = trimmed
    return short.strip(" ,.") or name


def position_text(raw):
    """Short position for the site and its role group (Director / Officer / Other)."""
    key = tidy(raw).lower().rstrip(".")
    if key in POSITIONS:
        return POSITIONS[key]
    if "board" in key or "director" in key or "chairman" in key:
        return tidy(raw), "Director"
    if any(word in key for word in ("officer", "executive", "ceo", "cfo", "managing", "head", "chief", "president",
                                    "manager", "vd", "verkställande")):
        return tidy(raw), "Officer"
    return tidy(raw) or "Insider", "Other"


def row_key(fields):
    """A stable key for a transaction line: everything except its status and amendment note."""
    parts = [tidy(fields.get(column, "")) for column in (
        "Publication date", "Issuer", "LEI-code", "Notifier", "Person discharging managerial responsibilities",
        "Position", "Closely associated", "Amendment", "Initial notification", "Linked to share option programme",
        "Nature of transaction", "Intrument type", "Instrument type", "Instrument name", "ISIN", "Transaction date",
        "Volume", "Unit", "Price", "Currency", "Trading venue")]
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


def filing_key(fields):
    """Groups the lines of one notification: published at the same second, same issuer and person."""
    parts = [tidy(fields.get(column, "")) for column in (
        "Publication date", "Issuer", "Notifier", "Person discharging managerial responsibilities")]
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def sanity(volume, price, value_eur):
    if volume is None or volume <= 0:
        return True, "no share count"
    if price is None or price <= 0:
        return True, "no price"
    if price > MAX_PRICE:
        return True, f"price over {MAX_PRICE:,} per share"
    if volume > MAX_VOLUME:
        return True, f"over {MAX_VOLUME:,} shares"
    if value_eur is not None and value_eur > MAX_VALUE_EUR:
        return True, f"value over €{MAX_VALUE_EUR / 1e9:.0f} billion"
    return False, None


# ------------------------------------------------------------------ parsing

def read_export(content):
    """Rows of the export as dicts keyed by column name (checks that the columns are there)."""
    text = decode_export(content)
    if "<html" in text[:3000].lower():
        raise ExportProblem("the export came back as a web page, not a data file")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    try:
        header = [tidy(column) for column in next(reader)]
    except StopIteration:
        raise ExportProblem("the export is empty (not even a header row)") from None
    while header and not header[-1]:
        header.pop()
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ExportProblem(f"the export lacks the column(s) {', '.join(missing)} (it has: {', '.join(header[:8])}...)")
    rows = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        rows.append(dict(zip(header, values)))
    return rows


def to_trade(fields, rate_on):
    """One export line -> a database row, or None when it isn't a buy or sell of shares.

    `rate_on(day, currency)` gives the number of units of the currency per euro on
    that day (or None when unknown)."""
    nature = tidy(fields.get("Nature of transaction", ""))
    kind = KINDS.get(nature.lower())
    if kind is None:
        return None
    instrument_type = tidy(fields.get("Intrument type") or fields.get("Instrument type") or "")
    if instrument_type.lower() not in INSTRUMENTS_KEPT:
        return None
    person = person_display_name(fields.get("Person discharging managerial responsibilities", ""))
    if not person or is_entity(person):
        return None
    issuer = company_display_name(fields.get("Issuer", ""))
    volume = to_number(fields.get("Volume"))
    price = to_number(fields.get("Price"))
    currency = tidy(fields.get("Currency", "")).upper() or None
    trade_date = parse_day(fields.get("Transaction date", ""))
    value = volume * price if volume is not None and price is not None else None
    value_eur = None
    if value is not None and currency:
        rate = 1.0 if currency == "EUR" else rate_on(trade_date, currency) if trade_date else None
        value_eur = value / rate if rate else None
    suspect, reason = sanity(volume, price, value_eur)
    position, role = position_text(fields.get("Position", ""))
    notifier = person_display_name(fields.get("Notifier", ""))
    closely = yes(fields.get("Closely associated", ""))
    status = tidy(fields.get("Status", "")) or "Current"
    return {
        "country": COUNTRY,
        "row_key": row_key(fields),
        "filing_key": filing_key(fields),
        "published_at": parse_published(fields.get("Publication date", "")),
        "issuer_name": issuer,
        "issuer_short": company_short_name(issuer),
        "issuer_lei": tidy(fields.get("LEI-code", "")) or None,
        "notifier": notifier if closely and notifier and notifier.lower() != person.lower() else None,
        "person_name": person,
        "position": position,
        "role": role,
        "position_raw": tidy(fields.get("Position", "")),
        "closely_associated": closely,
        "is_amendment": yes(fields.get("Amendment", "")),
        "amendment_note": tidy(fields.get("Details of amendment", "")) or None,
        "initial": yes(fields.get("Initial notification", "")),
        "option_programme": yes(fields.get("Linked to share option programme", "")),
        "nature": nature,
        "kind": kind,
        "instrument_type": instrument_type,
        "instrument_name": tidy(fields.get("Instrument name", "")) or None,
        "isin": tidy(fields.get("ISIN", "")).upper() or None,
        "trade_date": trade_date,
        "volume": volume,
        "unit": tidy(fields.get("Unit", "")) or None,
        "price": price,
        "currency": currency,
        "value": value,
        "value_eur": value_eur,
        "venue": tidy(fields.get("Trading venue", "")) or None,
        "status": status,
        "suspect": suspect,
        "suspect_reason": reason,
    }


def parse_export(content, rate_on):
    """All buys and sells of shares in an export: (rows, counts)."""
    counts = {"lines": 0, "kept": 0, "buys": 0, "sells": 0, "other_nature": 0, "other_instrument": 0,
              "entities": 0, "suspect": 0, "not_current": 0}
    rows = []
    for fields in read_export(content):
        counts["lines"] += 1
        trade = to_trade(fields, rate_on)
        if trade is None:
            nature = tidy(fields.get("Nature of transaction", "")).lower()
            instrument = tidy(fields.get("Intrument type") or fields.get("Instrument type") or "").lower()
            if nature not in KINDS:
                counts["other_nature"] += 1
            elif instrument not in INSTRUMENTS_KEPT:
                counts["other_instrument"] += 1
            else:
                counts["entities"] += 1
            continue
        rows.append(trade)
        counts["kept"] += 1
        counts["buys" if trade["kind"] == "buy" else "sells"] += 1
        counts["suspect"] += trade["suspect"]
        counts["not_current"] += trade["status"].lower() != "current"
    return rows, counts


def register_search_url(person, issuer, trade_date):
    """A link to the register's own search, showing this person's notifications for the company."""
    from urllib.parse import quote
    url = ("https://marknadssok.fi.se/Publiceringsklient/en-GB/Search/Search?SearchFunctionType=Insyn"
           f"&Utgivare={quote(issuer or '')}&PersonILedandeSt%C3%A4llningNamn={quote(person or '')}")
    if trade_date:
        url += f"&Transaktionsdatum.From={trade_date:%Y-%m-%d}&Transaktionsdatum.To={trade_date:%Y-%m-%d}"
    return url + "&button=search"
