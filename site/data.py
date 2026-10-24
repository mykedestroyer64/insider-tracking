"""
data.py - reads the collector's tables and turns them into the objects the
website is built from: filers (members of Congress, and Senate candidates),
their trades, and the statistics shown on the pages. Reports come from both
chambers: the Senate's eFD and the House Clerk.

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
    chamber: str = "senate"
    filer: "Filer | None" = None

    @property
    def date_after_filing(self):
        """The trade date is later than the day the report was filed: a typo in the report
        (it is shown as written, but not counted)."""
        return bool(self.traded and self.received and self.traded > self.received)

    @property
    def traded_ok(self):
        """The trade date, unless it is impossible (after the filing)."""
        return None if self.date_after_filing else self.traded

    @property
    def days(self):
        if self.traded and self.received and not self.date_after_filing:
            return (self.received - self.traded).days
        return None

    @property
    def late(self):
        """Disclosed after the legal limit (amendments don't count: they re-report old trades)."""
        return (not self.is_amendment) and self.days is not None and self.days > LATE_AFTER_DAYS

    @property
    def counted(self):
        """Amendments usually repeat trades already reported, so they are shown but not counted."""
        return not self.is_amendment

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
    chamber: str = "senate"
    state_district: str = ""      # House only, e.g. CA11 (00 = the state's single, at-large seat)
    trades: list = field(default_factory=list)

    @property
    def oldest_trade(self):
        return min((t.traded_ok for t in self.trades if t.traded_ok), default=None)

    @property
    def delay(self):
        """Days from the report's oldest trade to its filing: the delay the 45-day rule is about."""
        if self.oldest_trade and self.received:
            return max(0, (self.received - self.oldest_trade).days)
        return None

    @property
    def late(self):
        """Filed more than 45 days after a trade in it (amendments don't count: they re-file old trades)."""
        return (not self.is_amendment) and self.delay is not None and self.delay > LATE_AFTER_DAYS


@dataclass
class Filer:
    key: str                      # "chamber|last|first" as the source writes it (plus the state for the House)
    first_name: str
    last_name: str
    name: str                     # display name
    slug: str
    filer_type: str               # Senator, Former Senator, Representative, Former Representative, Candidate
    chamber: str = "senate"       # the chamber of the newest report
    district: str = ""            # House district of the newest House report, e.g. CA11
    party: str | None = None      # D, R, I
    state: str | None = None      # two letters
    bioguide: str | None = None
    match_note: str = ""
    reports: list = field(default_factory=list)
    trades: list = field(default_factory=list)

    @property
    def is_member(self):
        """A senator or representative, now or in the past (not a candidate)."""
        kind = (self.filer_type or "").lower()
        return "senator" in kind or "representative" in kind

    @property
    def is_senator(self):
        return "senator" in (self.filer_type or "").lower()

    @property
    def in_house(self):
        """Their latest seat is in the House."""
        return "representative" in (self.filer_type or "").lower()

    @property
    def chamber_label(self):
        return "House" if self.in_house else "Senate"

    @property
    def district_label(self):
        """'CA-11', or 'AK-AL' for a state's single (at-large) seat."""
        match = re.fullmatch(r"([A-Z]{2})(\d{1,2})", self.district or "")
        if not self.in_house or not match:
            return ""
        number = int(match.group(2))
        return f"{match.group(1)}-{number}" if number else f"{match.group(1)}-AL"

    @property
    def party_state(self):
        """'R · AR' for a senator, 'D · CA-11' for a representative."""
        place = self.district_label or self.state
        parts = [part for part in (self.party, place) if part]
        return " · ".join(parts)

    @property
    def state_name(self):
        return STATE_NAMES.get(self.state or "", self.state or "")

    @property
    def description(self):
        """'Democrat · Pennsylvania · Senator', or '... · Representative, CA-11', with whatever is known."""
        party = {"D": "Democrat", "R": "Republican", "I": "Independent"}.get(self.party or "", self.party)
        role = self.filer_type
        if role and self.district_label:
            role = f"{role}, {self.district_label}"
        parts = [part for part in (party, self.state_name, role) if part]
        return " · ".join(parts)

    @property
    def counted_trades(self):
        return [t for t in self.trades if t.counted]

    @property
    def filed_reports(self):
        """Reports with trades read from them, amendments aside (the unit of the 45-day rule)."""
        return [r for r in self.reports if r.trades and not r.is_amendment]

    def stats(self):
        trades = self.counted_trades
        reports = self.filed_reports
        return {
            "trades": len(trades),
            "buys": sum(1 for t in trades if t.group == "buy"),
            "sells": sum(1 for t in trades if t.group == "sell"),
            "amendment_trades": sum(1 for t in self.trades if not t.counted),
            "reports": len(reports),
            "late": sum(1 for r in reports if r.late),
            "late_trades": sum(1 for t in trades if t.late),
            "stocks": len({slug for t in trades for slug in t.stock_slugs}),
            "last_traded": max((t.traded_ok for t in trades if t.traded_ok), default=None),
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
       to_char(first_seen_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS'),
       chamber, coalesce(state_district, '')
from reports
order by date_received desc nulls last, report_path
"""
# Before the first House collector run the database has no chamber columns yet
REPORT_SQL_SENATE_ONLY = REPORT_SQL.replace("chamber, coalesce(state_district, '')", "'senate', ''")

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


HAS_CHAMBER_SQL = """
select count(*)::text from information_schema.columns
where table_name = 'reports' and column_name = 'chamber'
"""


def load(conn):
    """Read everything the site needs. Returns a Dataset."""
    reports = {}
    has_chamber = fetch_all(conn, HAS_CHAMBER_SQL)[0][0] != "0"
    for row in fetch_all(conn, REPORT_SQL if has_chamber else REPORT_SQL_SENATE_ONLY):
        report = Report(
            path=row[0], kind=row[1], status=row[2], first_name=row[3], last_name=row[4],
            filer_name=row[5], filer_type=row[6], title=row[7], is_amendment=row[8] == "1",
            received=parse_date(row[9]), url=row[10], transaction_count=parse_int(row[11]) or 0,
            first_seen=datetime.fromisoformat(row[12]) if row[12] else None,
            chamber=row[13] or "senate", state_district=row[14] or "",
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
            chamber=report.chamber,
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
        self.report_by_path = {r.path: r for r in reports}
        for trade in self.trades:
            report = self.report_by_path.get(trade.report_path)
            if report is not None:
                report.trades.append(trade)
        self.filers = self._group_filers()
        for trade in self.trades:
            trade.filer = self.filer_by_report.get(trade.report_path)
        self.stocks = self._group_stocks()

    # ---- grouping

    def _group_filers(self):
        filers = {}
        self.filer_by_report = {}
        for report in sorted(self.reports, key=lambda r: (r.received or date.min), reverse=True):
            # One filer per chamber and name (and state, in the House). Someone who served in
            # both chambers is joined into one person later, once matched to the legislators list.
            key = f"{report.chamber}|{report.last_name}|{report.first_name}"
            if report.chamber == "house":
                key += f"|{report.state_district[:2]}"
            filer = filers.get(key)
            if filer is None:
                filer = Filer(key=key, first_name=report.first_name, last_name=report.last_name,
                              name=display_name(report.first_name, report.last_name),
                              slug=slugify(display_name(report.first_name, report.last_name)),
                              filer_type=report.filer_type,   # newest report first: current type
                              chamber=report.chamber)
                filers[key] = filer
            if report.chamber == "house" and not filer.district:
                filer.district = report.state_district
                if not filer.state and len(report.state_district) >= 2:
                    filer.state = report.state_district[:2]
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
            name = stock["names"].most_common(1)[0][0] if stock["names"] else stock["ticker"]
            # some reports name the option rather than the company: "NVIDIA Corp Option Type: Call ..."
            stock["name"] = re.split(r"\s+(?:Option Type|Call Option|Put Option|Strike price)\b", name, 1)[0].strip(" -,") or name
            stock["trades"].sort(key=lambda t: (t.received or date.min, t.traded or date.min), reverse=True)
            counted = [t for t in stock["trades"] if t.counted]
            stock["count"] = len(counted)
            stock["filers"] = sorted({t.filer.slug: t.filer for t in counted if t.filer}.values(),
                                     key=lambda f: f.name)
            stock["buys"] = sum(1 for t in counted if t.group == "buy")
            stock["sells"] = sum(1 for t in counted if t.group == "sell")
            stock["last_received"] = max((t.received for t in counted if t.received), default=None)
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
        return [t for t in self.trades if t.counted and t.received and t.received > start]

    def filed_reports(self, days=None):
        """Reports with trades, amendments aside, optionally only those received in the last N days."""
        start = self.today - timedelta(days=days) if days else None
        return [r for r in self.reports if r.trades and not r.is_amendment and r.received
                and (start is None or r.received > start)]

    def overview_stats(self):
        recent = self.recent_window(30)
        tickers = Counter(ticker.upper() for t in recent for ticker in t.tickers)
        most = tickers.most_common(1)
        recent_reports = self.filed_reports(30)
        return {
            "trades_30d": len(recent),
            "filers_30d": len({t.filer.key for t in recent if t.filer}),
            "reports_30d": len(recent_reports),
            "late_30d": sum(1 for r in recent_reports if r.late),
            "top_ticker": most[0][0] if most else None,
            "top_ticker_slug": stock_slug(most[0][0]) if most else None,
            "top_ticker_count": most[0][1] if most else 0,
            "trades_total": sum(1 for t in self.trades if t.counted),
            "reports_total": len(self.reports),
            "filers_total": len(self.filers),
            "paper_reports": sum(1 for r in self.reports if r.kind == "paper"),
            "unread_reports": sum(1 for r in self.reports if r.status in ("to_read", "failed")),
        }

    def late_reports(self):
        """Reports filed after the 45-day limit, longest delay first."""
        return sorted((r for r in self.filed_reports() if r.late), key=lambda r: (r.delay, r.received), reverse=True)

    def late_trades(self):
        return sorted((t for t in self.trades if t.late), key=lambda t: (t.days, t.received), reverse=True)

    def delay_histogram(self, months=12):
        """Reports (not amendments) by days from their oldest trade to their filing, last N months."""
        bins = [(0, 7, "0–7"), (8, 15, "8–15"), (16, 22, "16–22"), (23, 30, "23–30"), (31, 37, "31–37"),
                (38, 45, "38–45"), (46, 60, "46–60"), (61, 90, "61–90"), (91, None, "90+")]
        counts = [0] * len(bins)
        total = late = 0
        for report in self.filed_reports(30 * months):
            if report.delay is None:
                continue
            total += 1
            late += report.late
            for i, (lo, hi, _) in enumerate(bins):
                if report.delay >= lo and (hi is None or report.delay <= hi):
                    counts[i] += 1
                    break
        return {"labels": [b[2] for b in bins], "counts": counts, "late_from_index": 6,
                "total": total, "late": late}

    def monthly_counts(self):
        """Trades disclosed per month, from the first month with data to this month."""
        if not self.trades:
            return []
        by_month = Counter((t.received.year, t.received.month) for t in self.trades if t.received and t.counted)
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
            if trade.counted and trade.received and trade.received >= starts[0]:
                index = (trade.received - starts[0]).days // 7
                if 0 <= index < weeks:
                    counts[index] += 1
        return [(start, counts.get(i, 0)) for i, start in enumerate(starts)]

    def years(self):
        return sorted({t.received.year for t in self.trades if t.received}, reverse=True)
