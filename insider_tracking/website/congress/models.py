"""
models.py - what the Congress pages are built from: trades, the reports they come in, and the
people who file them (members of Congress, and Senate candidates).
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ..names import stock_slug

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
    perf: object = None          # how the stock did afterwards (returns.Perf), when known

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
