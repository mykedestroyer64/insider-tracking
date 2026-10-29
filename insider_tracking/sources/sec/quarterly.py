"""
quarterly.py - step 2 of the SEC collector: the SEC's quarterly data files, newest first,
QUARTERS_PER_RUN per run, back to the first quarter wanted.

Each file holds every Form 4 of a quarter; only officers' and directors' buys and sells are
kept. The quarters loaded so far are the bookmark sec_quarters_done.
"""

import tempfile
from pathlib import Path

from ...core.errors import SiteStop
from ...core.status import CHANGED
from ...core.text import plural
from ...web.checks import unexpected
from ..ports import Bookmarks, InsiderStore, Note, SecGateway
from .parsing import FilingProblem, parse_quarter
from .quarters import quarter_name

QUARTERS_PER_RUN = 2


class QuarterlyFiles:
    """stats: the run's counts; first_quarter: (year, quarter), how far back the files are loaded."""

    def __init__(self, site: SecGateway, filings: InsiderStore, bookmarks: Bookmarks, stats: dict, note: Note,
                 first_quarter: tuple):
        self.site = site
        self.filings = filings
        self.bookmarks = bookmarks
        self.stats = stats
        self.note = note
        self.first_quarter = first_quarter

    def load(self, files):
        """Load the files not loaded yet. files: {(year, quarter): file} as the SEC lists them;
        returns the step's summary line."""
        done = set(filter(None, (self.bookmarks.get("sec_quarters_done") or "").split(",")))
        wanted = sorted((q for q in files if q >= self.first_quarter and quarter_name(*q) not in done),
                        reverse=True)
        if not wanted:
            return (f"complete - every quarterly file from {quarter_name(*self.first_quarter)} "
                    f"to {quarter_name(*max(files))} is loaded")
        loaded = []
        for year, quarter in wanted[:QUARTERS_PER_RUN]:
            loaded.append(self.load_one(year, quarter, files[(year, quarter)], done))
        for line in loaded:
            self.note(line)
        left = len(wanted) - len(loaded)
        return f"loaded {len(loaded)}; {left} quarterly file{'s' if left != 1 else ''} left to load"

    def load_one(self, year, quarter, file, done):
        """Download, read and save one quarter's file; its note line."""
        name = quarter_name(year, quarter)
        with tempfile.TemporaryDirectory() as folder:
            response = self.site.quarterly_file(file)
            if not response.content.startswith(b"PK"):
                raise unexpected(response, f"the {name} file is not a ZIP file")
            path = Path(folder) / f"{name}.zip"
            path.write_bytes(response.content)
            try:
                filings, trades, counts = parse_quarter(path)
            except (FilingProblem, OSError) as problem:
                raise SiteStop(CHANGED, f"the {name} file could not be read: {problem}") from None
        new = self.filings.save(filings, trades, source=name)
        done.add(name)
        self.bookmarks.set("sec_quarters_done", ",".join(sorted(done)))
        self.stats["filings_saved"] += len(filings)
        self.stats["trades_saved"] += len(trades)
        self.stats["suspect"] += counts["suspect"]
        return (f"{name}: {len(response.content) / 1e6:.0f} MB, {counts['form4']:,} Form 4s, kept "
                f"{plural(len(trades), 'trade')} by officers and directors in {len(filings):,} filings "
                f"({new:,} new)")
