"""
build.py - builds the Insider Tracking website from the collectors' database.

GitHub Actions runs it after every collector run (see .github/workflows/site.yml) and publishes
the result to GitHub Pages. You can also run it on your computer:

    python -m insider_tracking site build --out _site

The build: read the database; add party and state from the legislators list; attach the
returns; write every page (pages/), in order.
"""

import argparse

from ..core.errors import DatabaseError
from ..db.database import Database
from ..runs.settings import Settings
from .config import CACHE, OVERRIDES, STATIC, TEMPLATES, SiteConfig
from .congress import loader as congress_loader
from .congress.data import CongressData
from .insiders import loader as insider_loader
from .pages import PAGES, SiteContext
from .people.dataset import PublicDataset
from .people.legislators import Legislator
from .people.matcher import Matcher
from .people.overrides import load_overrides
from .render import Renderer, template_globals
from .returns import loader as returns_loader
from .returns.rules import attach
from .writer import SiteWriter


class SiteBuild:
    """legislators: where the members' records come from (a LegislatorRecords: the public
    dataset, or a fixed list in tests); overrides: the corrections file; pages: what is built,
    in order."""

    def __init__(self, legislators, overrides=OVERRIDES, pages=PAGES):
        self.legislators = legislators
        self.overrides = overrides
        self.pages = pages

    def parser(self):
        parser = argparse.ArgumentParser(description="Build the Insider Tracking website.")
        parser.add_argument("--out", default="_site", help="output folder (default: _site)")
        parser.add_argument("--base", help="base path on the domain, e.g. /insider-tracking")
        parser.add_argument("--site-url", help="full address of the site")
        parser.add_argument("--offline", action="store_true", help="don't download the legislators list")
        return parser

    def run(self, argv, platform):
        """The build: read the database, add party and state, attach the returns, write the pages."""
        args = self.parser().parse_args(argv)
        log = platform.console.line
        config = SiteConfig.read(platform.environ, args.base, args.site_url)
        log(f"Insider Tracking site build -> {args.out} (base path {config.base or '/'}, {config.url})")
        started = platform.clock.now()

        log("Reading the database…")
        congress, insiders, perfs = self.read(platform)
        log(f"  {len(congress.reports)} reports, {len(congress.trades)} trades, "
            f"{len(congress.filers)} filers; {len(insiders.trades)} insider trades; "
            f"data updated {congress.updated or 'never'}")

        log("Matching names to the legislators list…")
        self.add_people(congress, args.offline, platform)

        found = attach(perfs, congress.trades, insiders.trades)
        log(f"  returns: {len(perfs):,} stored, {found:,} trades shown with one")

        log("Writing pages…")
        writer = self.write(args.out, config, congress, insiders, platform)
        markets = ", ".join(f"{m.name} {sum(1 for t in insiders.trades if t.country == m.code):,}"
                            for m in insiders.markets)
        log(f"  {len(writer.pages)} pages, {len(congress.trades)} trades, {len(congress.filers)} filers, "
            f"{len(congress.stocks)} stocks, {len(insiders.trades)} insider trades by {len(insiders.people)} insiders "
            f"at {len(insiders.companies)} companies" + (f" ({markets})" if markets else ""))
        log(f"Done in {(platform.clock.now() - started).total_seconds():.1f} s")
        return 0

    def add_people(self, congress, offline, platform):
        """Party and state for each member of Congress: their names matched to the legislators
        list (and the corrections file)."""
        log = platform.console.line
        today = platform.clock.today()
        legislators = [Legislator(record, today) for record in self.legislators.records(offline, log)]
        matcher = Matcher(legislators, load_overrides(self.overrides, warn=platform.console.error), log=log)
        congress.apply_people(matcher)
        log(f"  {matcher.matched} filers matched")
        if matcher.unmatched:
            log(f"  {len(matcher.unmatched)} members of Congress without party/state "
                "(add them to site/overrides.json):")
            for filer in matcher.unmatched:
                log(f"    {filer.key!r}: {filer.match_note}")

    def write(self, out, config, congress, insiders, platform):
        """Every page, by the builders in order; returns the writer (it lists the pages written)."""
        build_id = str(int(platform.clock.now().timestamp()))
        renderer = Renderer(TEMPLATES, template_globals(config, congress, insiders, build_id))
        writer = SiteWriter(out, renderer, config.base)
        writer.start(STATIC)
        site = SiteContext(congress=congress, insiders=insiders, writer=writer, config=config,
                           now=platform.clock.now(), today=platform.clock.today())
        for page in self.pages:
            page.build(site)
        return writer

    def read(self, platform):
        """Everything the pages need from the database: (Congress data, insider data, returns)."""
        settings = Settings.read(platform.environ)
        if not settings.db_url:
            raise DatabaseError("SUPABASE_DB_URL is not set")
        db = Database(platform.connect(settings.db_url, settings.db_password))
        try:
            today = platform.clock.today()
            reports, trades, updated = congress_loader.load(db)
            congress = CongressData(reports, trades, updated, today)
            insiders = insider_loader.load(db, today)
            perfs = returns_loader.load(db)
        finally:
            db.close()
        return congress, insiders, perfs


def command():
    """`python -m insider_tracking site build`."""
    return SiteBuild(PublicDataset(CACHE))
