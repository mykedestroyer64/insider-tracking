"""
collector.py - the Swedish executives collector (Finansinspektionen's PDMR register).

GitHub Actions runs it every 3 hours (see .github/workflows/collect-sweden.yml). Each run:
  1. connects to the database (and adds the tables the first time)
  2. updates the euro exchange rates from the European Central Bank, so every trade in
     kronor (or dollars, or pounds) gets a euro figure next to it
  3. reads the register site's robots.txt, the rules it gives to scripts
  4. exports the notifications published in the last 45 days from the register (one
     request) and saves the buys and sells of shares in them
  5. loads the history, twelve months per run, back to July 2016 when the register started
Lines seen before are updated, never duplicated, so the windows can overlap and a
notification that is later revised or cancelled is marked as such.

It sends about 15 requests to Finansinspektionen per run, 2-3 seconds apart, with a
User-Agent that names the project and gives a contact email, and stops if the site refuses
it (no workarounds).
"""

from datetime import date, timedelta

from ...core.errors import SiteStop
from ...core.identity import user_agent
from ...core.status import BLOCKED, CHANGED, DB_ERROR, ERROR, SITE_DOWN
from ...core.text import plural
from ...db.europe import MarTrades
from ...db.fx import FxRates
from ...db.size import DatabaseSize
from ...db.state import StateStore
from ...runs.command import CollectorCommand
from ...runs.definition import CollectorDefinition
from ...runs.summary import whole_database_note
from ...web.client import PoliteClient, new_session
from ...web.pacing import RequestBudget
from ..ecb.rates import Rates
from ..ecb.site import EcbEndpoints, EcbSite
from .parsing import COUNTRY, STOCKHOLM, ExportProblem, parse_export
from .site import PACING, FiEndpoints, FiRegister

VERSION = "0.1"
EXPORT_CAP = 1000          # the register's export stops at this many lines: longer windows are split
HISTORY_VERSION = "2"      # bump to make the next run load the history again (v1 lost lines to the cap)
RECENT_DAYS = 45           # the window re-exported on every run (catches revisions and cancellations)
MONTHS_PER_RUN = 12        # history months loaded per run
FIRST_DAY = date(2016, 7, 3)   # the register starts with the Market Abuse Regulation
RATES_STALE_AFTER = 80     # days: with older rates than this the whole history file is fetched again
RATES_FROM = date(2015, 1, 1)   # rates older than this aren't kept

DEFINITION = CollectorDefinition(
    key="sweden",
    title="Swedish executives collector",
    version=VERSION,
    description="Collect Swedish executives' buys and sells (Finansinspektionen's register) into the database.",
    steps=("Connect to the database", "Update euro exchange rates (ECB)", "Check the register's rules for scripts",
           "Export the last 45 days", "Load history (12 months per run)"),
    max_requests=60,
    label_width=42,
    advice={
        BLOCKED: ["Finansinspektionen's site refused the collector, or its robots.txt rules scripts out.",
                  "Do not add workarounds. The next scheduled run tries again in 3 hours; if several runs",
                  "in a row are BLOCKED, stop the schedule and share this result. FI can be asked by email",
                  "(finansinspektionen@fi.se) whether a daily export is acceptable."],
        SITE_DOWN: ["The site had a server error. The next scheduled run tries again in 3 hours."],
        CHANGED: ["A page or file did not look the way the collector expects, so the site may work",
                  "differently now. Share this log so the collector can be updated."],
        DB_ERROR: ["Check the SUPABASE_DB_URL secret (it must be Supabase's Session pooler string),",
                   "the SUPABASE_DB_PASSWORD secret, and that the Supabase project isn't paused."],
        ERROR: ["The collector hit a problem it didn't expect. Share this log."],
    },
)


def as_date(value):
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return value


def last_business_day(today):
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


class SwedenSource:
    definition = DEFINITION

    def __init__(self, endpoints=FiEndpoints(), ecb_endpoints=EcbEndpoints(), pacing=PACING):
        self.endpoints = endpoints
        self.ecb_endpoints = ecb_endpoints
        self.pacing = pacing

    def start(self, context):
        return SwedenRun(context, self)


class SwedenRun:
    def __init__(self, context, source):
        self.context = context
        self.source = source
        self.trades = MarTrades(context.db)
        self.fx = FxRates(context.db)
        self.state = StateStore(context.db)
        self.size = DatabaseSize(context.db)
        self.today = context.started.astimezone(STOCKHOLM).date()
        self.client = self.register = self.ecb = self.rates = None
        self.stats = {"lines": 0, "kept": 0, "new": 0, "updated": 0, "suspect": 0, "skipped_nature": 0,
                      "skipped_instrument": 0, "skipped_entities": 0}

    # ------------------------------------------------------------- the run

    def prepare(self):
        return f"tables ready, {self.trades.counts(COUNTRY)['trades']:,} Swedish trades stored so far"

    def open(self):
        session = new_session(user_agent(VERSION, self.context.settings.email), compressed=True)
        self.client = PoliteClient(session, self.source.pacing, RequestBudget(self.context.max_requests),
                                   sleep=self.context.sleep)
        self.register = FiRegister(self.client, self.source.endpoints)
        self.ecb = EcbSite(self.client, self.source.ecb_endpoints)

    def steps(self):
        return [self.update_rates, self.register.robots_allow, self.export_recent, self.load_history]

    @property
    def requests_sent(self):
        return self.client.requests_sent if self.client else None

    def budget_used_up(self):
        return "this run's request budget is used up"

    # ----------------------------------------------------------- the steps

    def update_rates(self):
        """Step 2: make sure the euro rates cover every trade day."""
        today = self.today
        rates = Rates(self.fx.load())
        latest = rates.latest_day()
        fetched = 0
        if latest is None or latest < today - timedelta(days=RATES_STALE_AFTER):
            rows = [row for row in self.ecb.history_rows() if row[0] >= RATES_FROM]   # the registers start in 2016
            fetched = self.fx.save(rows)
            what = f"loaded the whole history ({fetched:,} new daily rates)"
        elif latest < last_business_day(today):
            fetched = self.fx.save(self.ecb.recent_rows())
            what = f"{fetched:,} new daily rates"
        else:
            what = "already up to date"
        if fetched:
            rates = Rates(self.fx.load())
        latest = rates.latest_day()
        # euro figures for rows saved while a rate was missing
        filled = {}
        for row in self.trades.without_eur(COUNTRY):
            rate = rates.rate_on(as_date(row["trade_date"]), row["currency"])
            if rate:
                filled[row["row_key"]] = row["value"] / rate
        if filled:
            self.trades.set_eur(COUNTRY, filled)
        self.rates = rates
        detail = f"{what}; newest rate {latest}" if latest else what
        if filled:
            detail += f"; euro values added to {plural(len(filled), 'older row')}"
        return detail

    def save_export(self, start, end, label):
        """Export one window of the register and save it. Returns (rows kept, new, updated, lines).

        The register's export never holds more than EXPORT_CAP lines, so a window that comes
        back full is split in two and both halves are exported again."""
        response = self.register.export(start, end)
        try:
            rows, counts = parse_export(response.content, self.rates.rate_on)
        except ExportProblem as problem:
            raise SiteStop(CHANGED, f"the export for {start}..{end} could not be read: {problem}", response) from None
        if counts["lines"] >= EXPORT_CAP and end > start:
            middle = start + (end - start) // 2
            first = self.save_export(start, middle, label)
            second = self.save_export(middle + timedelta(days=1), end, label)
            return tuple(a + b for a, b in zip(first, second))
        if counts["lines"] >= EXPORT_CAP:
            self.context.console.note(f"{start}: {counts['lines']:,} lines on one day, the export's limit; "
                                      "some may be missing")
        new, updated = self.trades.save(rows, source=f"{label} {start}..{end}")
        stats = self.stats
        stats["lines"] += counts["lines"]
        stats["kept"] += len(rows)
        stats["new"] += new
        stats["updated"] += updated
        stats["suspect"] += counts["suspect"]
        stats["skipped_nature"] += counts["other_nature"]
        stats["skipped_instrument"] += counts["other_instrument"]
        stats["skipped_entities"] += counts["entities"]
        return len(rows), new, updated, counts["lines"]

    def export_recent(self):
        """Step 4: the last RECENT_DAYS days of publications (one request)."""
        today = self.today
        start = today - timedelta(days=RECENT_DAYS)
        kept, new, updated, lines = self.save_export(start, today, "recent")
        if self.state.get("se_history_cursor") is None:
            self.state.set("se_history_cursor", start.isoformat())   # history continues from here
        self.state.set("se_recent_through", today.isoformat())
        return (f"{lines:,} lines published {start} to {today}; {kept:,} buys or sells of shares kept "
                f"({new:,} new, {updated:,} seen before)")

    def load_history(self):
        """Step 5: months before the loaded range, MONTHS_PER_RUN per run, back to FIRST_DAY."""
        if self.state.get("se_history_version") != HISTORY_VERSION:
            recent_from = as_date(self.state.get("se_recent_through")) or self.context.today
            self.state.set("se_history_cursor", (recent_from - timedelta(days=RECENT_DAYS)).isoformat())
            self.state.set("se_history_version", HISTORY_VERSION)
            self.context.console.note("the history is loaded again from the start (the way it is read changed)")
        cursor = as_date(self.state.get("se_history_cursor"))
        if cursor is None or cursor <= FIRST_DAY:
            return f"complete - the register is loaded back to {FIRST_DAY}"
        loaded = []
        for _ in range(MONTHS_PER_RUN):
            if cursor <= FIRST_DAY:
                break
            end = cursor - timedelta(days=1)
            start = max(end.replace(day=1), FIRST_DAY)
            kept, new, updated, lines = self.save_export(start, end, "history")
            loaded.append(f"{start:%Y-%m}: {lines:,} lines, {kept:,} kept ({new:,} new)")
            cursor = start
            self.state.set("se_history_cursor", cursor.isoformat())
        for line in loaded:
            self.context.console.note(line)
        if cursor <= FIRST_DAY:
            return f"loaded {len(loaded)} months; the history is complete back to {FIRST_DAY}"
        months_left = (cursor.year - FIRST_DAY.year) * 12 + cursor.month - FIRST_DAY.month
        return f"loaded {len(loaded)} months, back to {cursor}; about {months_left} months left"

    # ------------------------------------------------------- the summing up

    def read_totals(self):
        return self.trades.counts(COUNTRY)

    def outcome(self, totals):
        stats = self.stats
        if stats["kept"] == 0 and stats["lines"] == 0:
            return "- nothing new, everything is up to date"
        return (f"- {plural(stats['kept'], 'trade')} saved from {stats['lines']:,} register lines "
                f"({stats['new']:,} new, {stats['updated']:,} seen before)")

    def closing_lines(self, totals):
        stats, lines = self.stats, []
        if stats["lines"]:
            lines.append(f"Left out this run: {stats['skipped_nature']:,} lines that aren't buys or sells (subscriptions, "
                         f"option exercises, gifts...), {stats['skipped_instrument']:,} in other instruments (warrants, "
                         f"bonds...), {stats['skipped_entities']:,} reported for companies or funds rather than people")
        if totals:
            first, last = totals["first_published"], totals["last_published"]
            span = f"published {str(first)[:10]} to {str(last)[:10]}" if first and last else "nothing published yet"
            lines.append(f"Database now: {totals['trades']:,} Swedish trades ({totals['buys']:,} buys, "
                         f"{totals['sells']:,} sells, {totals['suspect']:,} suspect, {totals['not_current']:,} revised "
                         f"or cancelled) by {totals['people']:,} people at {totals['companies']:,} companies, {span}; "
                         f"table uses {totals['size']}" + whole_database_note(self.size))
        return lines


def command():
    """`python -m insider_tracking collect sweden`."""
    return CollectorCommand(SwedenSource())
