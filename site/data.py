"""
data.py - reads the collector's tables and turns them into the objects the
website is built from: filers (senators and candidates), their trades, and
the statistics shown on the pages.

Every query asks the database for text, so the same code works with any
driver and in tests. Dates and numbers are parsed here, in one place.
"""

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

LATE_AFTER_DAYS = 45          # the legal limit for reporting a trade

TRADE_LABELS = {
    "purchase": "Buy",
    "sale_full": "Sell (full)",
    "sale_partial": "Sell (partial)",
    "sale": "Sell",
    "exchange": "Exchange",
    "other": "Other",
}
TRADE_GROUPS = {"purchase": "buy", "sale_full": "sell", "sale_partial": "sell", "sale": "sell",
                "exchange": "exchange", "other": "other"}

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


# ------------------------------------------------------------------ helpers

def parse_date(text):
    return date.fromisoformat(text) if text else None


def parse_int(text):
    try:
        return int(text) if text not in (None, "") else None
    except ValueError:
        return None


def squash(text):
    """Lower-case letters only, accents removed: 'McConnell, Jr.' -> 'mcconnelljr'."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text.lower() if ch.isalnum())


def slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "unknown"


def display_name(first, last):
    """eFD stores 'A. Mitchell' + 'McConnell, Jr.'; show 'A. Mitchell McConnell Jr.'."""
    last_core, _, suffix = (last or "").partition(",")
    name = f"{(first or '').strip()} {last_core.strip()}".strip()
    if suffix.strip():
        name += f" {suffix.strip()}"
    return name


def last_name_core(last):
    """The family name without a suffix: 'McConnell, Jr.' -> 'McConnell'."""
    return (last or "").partition(",")[0].strip()


def fmt_date(value):
    return f"{value:%b} {value.day}, {value:%Y}" if value else ""


def fmt_number(value):
    return f"{value:,}" if value is not None else ""


# ------------------------------------------------------------------- models

@dataclass
class Trade:
    report_path: str
    row: int
    traded: date | None
    owner: str | None
    ticker: str | None
    asset_name: str | None
    asset_type: str | None
    transaction_type: str | None
    trade_type: str | None
    amount: str | None
    amount_min: int | None
    amount_max: int | None
    comment: str | None
    # filled in from the report
    received: date | None = None
    report_url: str = ""
    is_amendment: bool = False
    filer: "Filer | None" = None

    @property
    def days(self):
        if self.traded and self.received:
            return (self.received - self.traded).days
        return None

    @property
    def late(self):
        """Disclosed after the legal limit (amendments don't count: they re-report old trades)."""
        return (not self.is_amendment) and self.days is not None and self.days > LATE_AFTER_DAYS

    @property
    def label(self):
        return TRADE_LABELS.get(self.trade_type or "", self.transaction_type or "Other")

    @property
    def group(self):
        return TRADE_GROUPS.get(self.trade_type or "", "other")

    @property
    def tickers(self):
        """The ticker symbols in this row: usually one, two for an exchange ('T WBD')."""
        return [t for t in (self.ticker or "").split() if t not in ("--", "N/A")]

    @property
    def is_stock(self):
        return bool(self.tickers)

    @property
    def ticker_text(self):
        """'AAPL', or 'T → WBD' for an exchange of one stock for another."""
        return " → ".join(self.tickers)

    @property
    def stock_slug(self):
        """The page this trade is listed on first (an exchange is on both stocks' pages)."""
        return stock_slug(self.tickers[0]) if self.is_stock else None

    @property
    def stock_slugs(self):
        return [stock_slug(t) for t in self.tickers]


@dataclass
class Report:
    path: str
    kind: str
    status: str
    first_name: str
    last_name: str
    filer_name: str
    filer_type: str
    title: str
    is_amendment: bool
    received: date | None
    url: str
    transaction_count: int
    first_seen: datetime | None


@dataclass
class Filer:
    key: str                      # "last|first" exactly as eFD writes it
    first_name: str
    last_name: str
    name: str                     # display name
    slug: str
    filer_type: str               # Senator, Former Senator, Candidate, ...
    party: str | None = None      # D, R, I
    state: str | None = None      # two letters
    bioguide: str | None = None
    match_note: str = ""
    reports: list = field(default_factory=list)
    trades: list = field(default_factory=list)

    @property
    def is_senator(self):
        return "senator" in (self.filer_type or "").lower()

    @property
    def party_state(self):
        parts = [part for part in (self.party, self.state) if part]
        return " · ".join(parts)

    @property
    def state_name(self):
        return STATE_NAMES.get(self.state or "", self.state or "")

    @property
    def description(self):
        """'D · Pennsylvania · Senator', with whatever is known."""
        party = {"D": "Democrat", "R": "Republican", "I": "Independent"}.get(self.party or "", self.party)
        parts = [part for part in (party, self.state_name, self.filer_type) if part]
        return " · ".join(parts)

    def stats(self):
        trades = self.trades
        return {
            "trades": len(trades),
            "buys": sum(1 for t in trades if t.group == "buy"),
            "sells": sum(1 for t in trades if t.group == "sell"),
            "late": sum(1 for t in trades if t.late),
            "stocks": len({slug for t in trades for slug in t.stock_slugs}),
            "last_traded": max((t.traded for t in trades if t.traded), default=None),
            "last_received": max((t.received for t in trades if t.received), default=None),
            "paper_reports": sum(1 for r in self.reports if r.kind == "paper"),
            "unread_reports": sum(1 for r in self.reports if r.status in ("to_read", "failed")),
        }


def stock_slug(ticker):
    return re.sub(r"[^A-Z0-9.\-]", "", (ticker or "").upper()) or None


# ------------------------------------------------------------------ loading

REPORT_SQL = """
select report_path, kind, status, coalesce(first_name, ''), coalesce(last_name, ''),
       coalesce(filer_name, ''), coalesce(filer_type, ''), coalesce(title, ''),
       is_amendment::int::text, to_char(date_received, 'YYYY-MM-DD'), url,
       coalesce(transaction_count, 0)::text,
       to_char(first_seen_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
from reports
order by date_received desc nulls last, report_path
"""

TRADE_SQL = """
select report_path, row_number::text, to_char(transaction_date, 'YYYY-MM-DD'), owner, ticker,
       asset_name, asset_type, transaction_type, trade_type, amount,
       amount_min::text, amount_max::text, comment
from transactions
order by report_path, row_number
"""

UPDATED_SQL = """
select to_char(max(finished_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
from collector_runs where verdict = 'OK'
"""


def fetch_all(conn, sql):
    cursor = conn.cursor()
    cursor.execute(sql)
    rows = cursor.fetchall()
    conn.commit()
    return [tuple(None if v is None else str(v) for v in row) for row in rows]


def load(conn):
    """Read everything the site needs. Returns a Dataset."""
    reports = {}
    for row in fetch_all(conn, REPORT_SQL):
        report = Report(
            path=row[0], kind=row[1], status=row[2], first_name=row[3], last_name=row[4],
            filer_name=row[5], filer_type=row[6], title=row[7], is_amendment=row[8] == "1",
            received=parse_date(row[9]), url=row[10], transaction_count=parse_int(row[11]) or 0,
            first_seen=datetime.fromisoformat(row[12]) if row[12] else None,
        )
        reports[report.path] = report

    trades = []
    for row in fetch_all(conn, TRADE_SQL):
        report = reports.get(row[0])
        if report is None:
            continue
        trades.append(Trade(
            report_path=row[0], row=parse_int(row[1]) or 0, traded=parse_date(row[2]),
            owner=row[3], ticker=row[4], asset_name=row[5], asset_type=row[6],
            transaction_type=row[7], trade_type=row[8], amount=row[9],
            amount_min=parse_int(row[10]), amount_max=parse_int(row[11]), comment=row[12],
            received=report.received, report_url=report.url, is_amendment=report.is_amendment,
        ))

    updated_rows = fetch_all(conn, UPDATED_SQL)
    updated = datetime.fromisoformat(updated_rows[0][0]) if updated_rows and updated_rows[0][0] else None
    return Dataset(list(reports.values()), trades, updated)


# ------------------------------------------------------------------ dataset

class Dataset:
    """Reports and trades grouped by filer and by stock, plus the site's statistics."""

    def __init__(self, reports, trades, updated=None, today=None):
        self.reports = reports
        self.trades = trades
        self.updated = updated
        self.today = today or date.today()
        self.filers = self._group_filers()
        for trade in self.trades:
            trade.filer = self.filer_by_report.get(trade.report_path)
        self.stocks = self._group_stocks()

    # ---- grouping

    def _group_filers(self):
        filers = {}
        self.filer_by_report = {}
        for report in sorted(self.reports, key=lambda r: (r.received or date.min), reverse=True):
            key = f"{report.last_name}|{report.first_name}"
            filer = filers.get(key)
            if filer is None:
                filer = Filer(key=key, first_name=report.first_name, last_name=report.last_name,
                              name=display_name(report.first_name, report.last_name),
                              slug=slugify(display_name(report.first_name, report.last_name)),
                              filer_type=report.filer_type)   # newest report first: current type
                filers[key] = filer
            filer.reports.append(report)
            self.filer_by_report[report.path] = filer
        for trade in self.trades:
            filer = self.filer_by_report.get(trade.report_path)
            if filer is not None:
                filer.trades.append(trade)
        for filer in filers.values():
            filer.trades.sort(key=lambda t: (t.received or date.min, t.traded or date.min), reverse=True)
        # Make slugs unique (two filers with the same display name get a number)
        seen = Counter(f.slug for f in filers.values())
        used = set()
        for filer in sorted(filers.values(), key=lambda f: f.key):
            if seen[filer.slug] > 1:
                base, n = filer.slug, 2
                while filer.slug in used:
                    filer.slug = f"{base}-{n}"
                    n += 1
            used.add(filer.slug)
        return filers

    def _group_stocks(self):
        stocks = {}
        for trade in self.trades:
            for ticker, slug in zip(trade.tickers, trade.stock_slugs):   # an exchange counts for both stocks
                stock = stocks.setdefault(slug, {"ticker": ticker.upper(), "slug": slug,
                                                 "names": Counter(), "trades": []})
                stock["trades"].append(trade)
                if trade.asset_name and len(trade.tickers) == 1:
                    stock["names"][trade.asset_name] += 1
        for stock in stocks.values():
            stock["name"] = stock["names"].most_common(1)[0][0] if stock["names"] else stock["ticker"]
            stock["trades"].sort(key=lambda t: (t.received or date.min, t.traded or date.min), reverse=True)
            stock["filers"] = sorted({t.filer.slug: t.filer for t in stock["trades"] if t.filer}.values(),
                                     key=lambda f: f.name)
            stock["buys"] = sum(1 for t in stock["trades"] if t.group == "buy")
            stock["sells"] = sum(1 for t in stock["trades"] if t.group == "sell")
            stock["last_received"] = max((t.received for t in stock["trades"] if t.received), default=None)
        return stocks

    # ---- views

    def apply_people(self, matcher):
        """Fill in party, state and official names using a legislators matcher."""
        for filer in self.filers.values():
            matcher.apply(filer)
        self._merge_same_person()
        # Two different people can still share an official name: keep slugs unique
        used = set()
        for filer in sorted(self.filers.values(), key=lambda f: f.key):
            base, n = filer.slug, 2
            while filer.slug in used:
                filer.slug = f"{base}-{n}"
                n += 1
            used.add(filer.slug)
        self.stocks = self._group_stocks()

    def _merge_same_person(self):
        """eFD writes some names in several ways ('Mike Rounds' and 'M. Michael Rounds', or a
        candidate who later became a senator). Filers matched to the same official person
        become one, keeping the filer type of the newest report."""
        def newest(filer):
            return max((r.received for r in filer.reports if r.received), default=date.min)

        by_person, merged = {}, {}
        for filer in sorted(self.filers.values(), key=newest, reverse=True):
            person = filer.bioguide or filer.key
            primary = by_person.get(person)
            if primary is None:
                by_person[person] = filer
                merged[filer.key] = filer
                continue
            primary.reports.extend(filer.reports)
            primary.trades.extend(filer.trades)
            for report in filer.reports:
                self.filer_by_report[report.path] = primary
            for trade in filer.trades:
                trade.filer = primary
        for filer in by_person.values():
            filer.reports.sort(key=lambda r: (r.received or date.min), reverse=True)
            filer.trades.sort(key=lambda t: (t.received or date.min, t.traded or date.min), reverse=True)
        self.filers = merged

    def trades_sorted(self):
        return sorted(self.trades, key=lambda t: (t.received or date.min, t.report_path, t.row), reverse=True)

    def recent_window(self, days):
        start = self.today - timedelta(days=days)
        return [t for t in self.trades if t.received and t.received > start]

    def overview_stats(self):
        recent = self.recent_window(30)
        tickers = Counter(ticker.upper() for t in recent for ticker in t.tickers)
        most = tickers.most_common(1)
        return {
            "trades_30d": len(recent),
            "filers_30d": len({t.filer.key for t in recent if t.filer}),
            "late_30d": sum(1 for t in recent if t.late),
            "top_ticker": most[0][0] if most else None,
            "top_ticker_slug": stock_slug(most[0][0]) if most else None,
            "top_ticker_count": most[0][1] if most else 0,
            "trades_total": len(self.trades),
            "reports_total": len(self.reports),
            "filers_total": len(self.filers),
            "paper_reports": sum(1 for r in self.reports if r.kind == "paper"),
            "unread_reports": sum(1 for r in self.reports if r.status in ("to_read", "failed")),
        }

    def late_trades(self):
        return sorted((t for t in self.trades if t.late), key=lambda t: (t.days, t.received), reverse=True)

    def delay_histogram(self, months=12):
        """Counts of trades (not amendments) by days from trade to disclosure, last N months."""
        start = self.today - timedelta(days=30 * months)
        bins = [(0, 7, "0–7"), (8, 15, "8–15"), (16, 22, "16–22"), (23, 30, "23–30"), (31, 37, "31–37"),
                (38, 45, "38–45"), (46, 60, "46–60"), (61, 90, "61–90"), (91, None, "90+")]
        counts = [0] * len(bins)
        total = late = 0
        for trade in self.trades:
            if trade.is_amendment or trade.days is None or not trade.received or trade.received <= start:
                continue
            days = max(0, trade.days)
            total += 1
            if days > LATE_AFTER_DAYS:
                late += 1
            for i, (lo, hi, _) in enumerate(bins):
                if days >= lo and (hi is None or days <= hi):
                    counts[i] += 1
                    break
        return {"labels": [b[2] for b in bins], "counts": counts, "late_from_index": 6,
                "total": total, "late": late}

    def monthly_counts(self):
        """Trades disclosed per month, from the first month with data to this month."""
        if not self.trades:
            return []
        by_month = Counter((t.received.year, t.received.month) for t in self.trades if t.received)
        first = min(by_month)
        y, m = first
        series = []
        while (y, m) <= (self.today.year, self.today.month):
            series.append((date(y, m, 1), by_month.get((y, m), 0)))
            m += 1
            if m == 13:
                y, m = y + 1, 1
        return series

    def weekly_counts(self, weeks=13):
        """Trades disclosed per week for the last N weeks (weeks start on Monday)."""
        this_monday = self.today - timedelta(days=self.today.weekday())
        starts = [this_monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
        counts = Counter()
        for trade in self.trades:
            if trade.received and trade.received >= starts[0]:
                index = (trade.received - starts[0]).days // 7
                if 0 <= index < weeks:
                    counts[index] += 1
        return [(start, counts.get(i, 0)) for i, start in enumerate(starts)]

    def years(self):
        return sorted({t.received.year for t in self.trades if t.received}, reverse=True)
