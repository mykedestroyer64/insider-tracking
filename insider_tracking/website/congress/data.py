"""
data.py - the Congress data the pages show: reports and trades grouped by filer and by
stock, and the statistics of the overview, the late filings and the charts.
"""

import re
from collections import Counter
from datetime import date, timedelta

from ..names import display_name, free_slug, slugify, stock_slug
from .models import Filer


class CongressData:
    """Reports and trades grouped by filer and by stock, plus the site's statistics."""

    def __init__(self, reports, trades, updated, today):
        self.reports = reports
        self.trades = trades
        self.updated = updated
        self.today = today
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
                filer.slug = free_slug(filer.slug, used)
            else:
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
            filer.slug = free_slug(filer.slug, used)
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
