"""
build.py - builds the Insider Tracking website from the collector's database.

GitHub Actions runs this after every collector run (see .github/workflows/site.yml)
and publishes the result to GitHub Pages. You can also run it on your computer:

    python site/build.py --out _site

Settings (environment variables, the same secrets the collector uses):
  SUPABASE_DB_URL, SUPABASE_DB_PASSWORD   the database
  SITE_BASE_PATH   where the site lives on its domain, e.g. "/insider-tracking"
                   (worked out from GITHUB_REPOSITORY when not set; "/" for a custom domain)
  SITE_URL         the full address, e.g. "https://mykedestroyer64.github.io/insider-tracking"

The output folder is a complete static website: HTML pages, a few JSON files
for the search box and the Trades page, the stylesheet, fonts and a feed.
"""

import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))            # for database.py in the repository root

import charts                                    # noqa: E402
import data as sitedata                          # noqa: E402
import people                                    # noqa: E402
from jinja2 import Environment, FileSystemLoader, select_autoescape   # noqa: E402

NAV = [
    ("home", "/", "Overview", '<rect x="2" y="2" width="5" height="5"></rect><rect x="9" y="2" width="5" height="5"></rect><rect x="2" y="9" width="5" height="5"></rect><rect x="9" y="9" width="5" height="5"></rect>'),
    ("trades", "/trades/", "Trades", '<path d="M2 5.5h10M9.5 3l2.5 2.5L9.5 8"></path><path d="M14 10.5H4M6.5 8L4 10.5 6.5 13"></path>'),
    ("congress", "/congress/", "Congress", '<circle cx="8" cy="5.5" r="3"></circle><path d="M2.5 14c.6-3 2.7-4.5 5.5-4.5s4.9 1.5 5.5 4.5"></path>'),
    ("stocks", "/stocks/", "Stocks", '<path d="M2 13.5l3.5-4 3 2.5L14 5"></path><path d="M10.5 5H14v3.5"></path>'),
    ("portfolios", "/portfolios/", "Portfolios", '<rect x="2" y="5" width="12" height="8.5" rx="1"></rect><path d="M5.5 5V3.5A1.5 1.5 0 0 1 7 2h2a1.5 1.5 0 0 1 1.5 1.5V5M2 9h12"></path>'),
    ("late", "/late/", "Late filings", '<circle cx="8" cy="8" r="6.5"></circle><path d="M8 4.5V8l2.5 1.5"></path>'),
    ("alerts", "/alerts/", "Alerts", '<path d="M4 11V7.5a4 4 0 0 1 8 0V11l1 1.5H3L4 11z"></path><path d="M6.5 14h3"></path>'),
    ("about", "/about/", "About", '<circle cx="8" cy="8" r="6.5"></circle><path d="M8 7v4M8 4.8v.4"></path>'),
]
ICON = '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" class="icon">{}</svg>'
LATEST_ON_HOME = 12
TOP_STOCKS_ON_MEMBER_PAGE = 10
FEED_ENTRIES = 50


def log(message):
    print(message, flush=True)


# ------------------------------------------------------------- small helpers

def headline(trade):
    name = trade.filer.name if trade.filer else "Someone"
    verb = {"buy": "bought", "sell": "sold", "exchange": "exchanged"}.get(trade.group, "traded")
    what = trade.ticker_text if trade.is_stock else (trade.asset_type or "an asset").lower()
    if not trade.is_stock and trade.asset_type:
        what = {"corporate bond": "a bond", "municipal security": "a municipal bond",
                "mutual fund": "a fund", "stock option": "stock options"}.get(what, what)
    return f"{name} {verb} {what}"


SHORT_ASSET_TYPES = {   # for the narrow "Stock" column of the home page's latest trades
    "government security": "Gov. bond", "corporate bond": "Bond", "municipal security": "Muni bond",
    "mutual fund": "Fund", "exchange traded fund": "ETF", "non-public stock": "Private stock",
    "business interest": "Business", "ownership interest": "Business", "stock option": "Options",
    "private fund": "Private fund", "cryptocurrency": "Crypto", "other securities": "Other",
    "asset-backed security": "Bond", "real property": "Property",
}


def short_asset(asset_type):
    return SHORT_ASSET_TYPES.get((asset_type or "").lower(), asset_type or "Other")


def days_text(trade):
    if trade.date_after_filing:
        return "–, the trade date in the report is later than its filing date"
    if trade.days is None:
        return "–"
    if trade.late:
        return f"{trade.days}, late ({trade.days - sitedata.LATE_AFTER_DAYS} days past the 45-day limit)"
    if trade.is_amendment:
        return f"{trade.days} (amendment)"
    return f"{trade.days}, within the limit"


def trade_record(trade):
    """The compact JSON record used by the Trades page and the home page's panel."""
    filer = trade.filer
    return {
        "d": trade.traded.isoformat() if trade.traded else None,
        "r": trade.received.isoformat() if trade.received else None,
        "sn": filer.name if filer else "", "ss": filer.slug if filer else "",
        "sp": (filer.party_state or filer.filer_type) if filer else "",
        "k": trade.ticker_text if trade.is_stock else None, "ks": trade.stock_slug,
        "a": trade.asset_name, "at": trade.asset_type,
        "g": trade.group, "tl": trade.label,
        "am": trade.amount, "mn": trade.amount_min, "mx": trade.amount_max,
        "o": trade.owner, "dy": trade.days, "l": 1 if trade.late else 0,
        "x": 1 if trade.is_amendment else 0, "u": trade.report_url, "c": trade.comment,
        "df": 1 if trade.date_after_filing else 0,
        "ch": "H" if trade.chamber == "house" else "S",
    }


def home_trade_json(trade):
    record = trade_record(trade)
    record["headline"] = headline(trade)
    record["who"] = trade.filer.description if trade.filer else ""
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def rfc3339(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return f"{value.isoformat()}T00:00:00Z"


def site_defaults():
    repo = os.environ.get("GITHUB_REPOSITORY", "mykedestroyer64/insider-tracking")
    owner, _, name = repo.partition("/")
    raw = (os.environ.get("SITE_BASE_PATH") or "").strip()
    if raw:                                   # "/" means the root of its own domain
        base = "" if raw == "/" else "/" + raw.strip("/")
    else:
        base = "" if name.lower() == f"{owner.lower()}.github.io" else f"/{name}"
    url = os.environ.get("SITE_URL") or f"https://{owner.lower()}.github.io{base}"
    return repo, base, url.rstrip("/")


# --------------------------------------------------------------------- build

class Builder:
    def __init__(self, dataset, out, base, site_url, repo):
        self.ds = dataset
        self.out = Path(out)
        self.base = base
        self.site_url = site_url
        self.repo = repo
        self.pages = []           # for the sitemap
        self.env = Environment(loader=FileSystemLoader(str(HERE / "templates")),
                               autoescape=select_autoescape(["html", "xml"]),
                               trim_blocks=True, lstrip_blocks=True)
        self.env.globals.update({
            "base": base, "site_url": site_url, "repo": repo,
            "build_id": str(int(time.time())),
            "updated": (f"{dataset.updated:%b} {dataset.updated.day}, {dataset.updated:%Y} "
                        f"{dataset.updated:%H:%M} UTC") if dataset.updated else "",
            "nav": [{"id": i, "href": h, "label": l, "icon": ICON.format(svg)} for i, h, l, svg in NAV],
            "fmt_date": sitedata.fmt_date, "fmt_number": sitedata.fmt_number,
            "headline": headline, "days_text": days_text, "trade_json": home_trade_json,
            "short_asset": short_asset,
        })

    def write(self, path, text):
        target = self.out / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def redirect(self, path, to):
        """A small page that sends visitors (and search engines) to a page's new address."""
        self.write(path, self.env.get_template("redirect.html").render(to=self.base + to))

    def page(self, template, path, **context):
        html = self.env.get_template(template).render(page=context.pop("page", None), **context)
        self.write(path, html)
        if path.endswith("index.html"):
            self.pages.append(path[: -len("index.html")])

    # ---- pages

    def build(self):
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        shutil.copytree(HERE / "static", self.out / "static")
        (self.out / ".nojekyll").write_text("")
        ds = self.ds

        self.build_home()
        self.build_trades()
        self.build_members()
        self.build_stocks()
        self.build_late()
        self.page("portfolios.html", "/portfolios/index.html", page="portfolios")
        self.page("alerts.html", "/alerts/index.html", page="alerts")
        self.page("about.html", "/about/index.html", page="about")
        self.page("404.html", "/404.html", page=None)
        self.build_data_files()
        self.build_feed()
        self.build_sitemap()
        log(f"  {len(self.pages)} pages, {len(ds.trades)} trades, {len(ds.filers)} filers, "
            f"{len(ds.stocks)} stocks")

    def build_home(self):
        ds = self.ds
        activity = charts.activity_charts(ds)
        latest = [t for t in ds.trades_sorted() if t.counted][:LATEST_ON_HOME]
        self.page("index.html", "/index.html", page="home",
                  stats=ds.overview_stats(), charts=activity,
                  charts_json=json.dumps(activity, separators=(",", ":")),
                  histogram_svg=charts.histogram_svg(ds.delay_histogram()), latest=latest)

    def build_trades(self):
        ds = self.ds
        filers = sorted((f for f in ds.filers.values() if f.trades), key=lambda f: f.name)
        self.page("trades.html", "/trades/index.html", page="trades", years=ds.years(), filers=filers)

    def build_members(self):
        ds = self.ds
        with_stats = [(f, f.stats()) for f in ds.filers.values()]
        members = sorted((x for x in with_stats if x[0].is_member), key=lambda x: (-x[1]["trades"], x[0].name))
        others = sorted((x for x in with_stats if not x[0].is_member), key=lambda x: (-x[1]["trades"], x[0].name))
        self.page("congress.html", "/congress/index.html", page="congress",
                  members=members, candidates=others,
                  senate_count=sum(1 for f, _ in members if not f.in_house),
                  house_count=sum(1 for f, _ in members if f.in_house))
        # The pages used to live under /senators/: keep those links working
        self.redirect("/senators/index.html", "/congress/")
        for filer, stats in with_stats:
            by_year = Counter(t.received.year for t in filer.counted_trades if t.received)
            per_stock = defaultdict(lambda: {"trades": 0, "buys": 0, "sells": 0, "last": None})
            for t in filer.counted_trades:
                for ticker, slug in zip(t.tickers, t.stock_slugs):
                    s = per_stock[slug]
                    s.update(ticker=ticker.upper(), slug=slug, name=ds.stocks[slug]["name"])
                    s["trades"] += 1
                    s["buys"] += t.group == "buy"
                    s["sells"] += t.group == "sell"
                    when = t.traded_ok or t.received
                    if when and (s["last"] is None or when > s["last"]):
                        s["last"] = when
            top = sorted(per_stock.values(), key=lambda s: (-s["trades"], s["ticker"]))[:TOP_STOCKS_ON_MEMBER_PAGE]
            self.page("member.html", f"/congress/{filer.slug}/index.html", page="congress",
                      filer=filer, stats=stats, top_stocks=top,
                      chambers=sorted({r.chamber for r in filer.reports}),
                      yearly_svg=charts.yearly_bars_svg(by_year, filer.name) if len(by_year) > 1 else "")
            if any(r.chamber == "senate" for r in filer.reports):
                self.redirect(f"/senators/{filer.slug}/index.html", f"/congress/{filer.slug}/")

    def build_stocks(self):
        ds = self.ds
        stocks = sorted(ds.stocks.values(), key=lambda s: (-s["count"], s["ticker"]))
        self.page("stocks.html", "/stocks/index.html", page="stocks", stocks=[s for s in stocks if s["count"]])
        for stock in stocks:
            by_year = Counter(t.received.year for t in stock["trades"] if t.received and t.counted)
            rows = {}
            for t in stock["trades"]:
                if not t.filer or not t.counted:
                    continue
                row = rows.setdefault(t.filer.slug, {"filer": t.filer, "trades": 0, "buys": 0, "sells": 0, "last": None})
                row["trades"] += 1
                row["buys"] += t.group == "buy"
                row["sells"] += t.group == "sell"
                when = t.traded_ok or t.received
                if when and (row["last"] is None or when > row["last"]):
                    row["last"] = when
            self.page("stock.html", f"/stocks/{stock['slug']}/index.html", page="stocks", stock=stock,
                      by_filer=sorted(rows.values(), key=lambda r: (-r["trades"], r["filer"].name)),
                      yearly_svg=charts.yearly_bars_svg(by_year, stock["ticker"]) if len(by_year) > 1 else "")

    def build_late(self):
        ds = self.ds
        late = ds.late_reports()
        rows = {}
        for report in late:
            filer = ds.filer_by_report.get(report.path)
            if not filer:
                continue
            row = rows.setdefault(filer.key, {"filer": filer, "late": 0, "late_trades": 0, "longest": 0})
            row["late"] += 1
            row["late_trades"] += sum(1 for t in report.trades if t.late)
            row["longest"] = max(row["longest"], report.delay)
        for row in rows.values():
            total = len(row["filer"].filed_reports)
            row["total"] = total
            row["share"] = round(100 * row["late"] / total) if total else 0
        by_filer = sorted(rows.values(), key=lambda r: (-r["late"], -r["share"], r["filer"].name))
        year_ago = ds.filed_reports(365)
        self.page("late.html", "/late/index.html", page="late", late=late, by_filer=by_filer,
                  hist=ds.delay_histogram(), filer_of=ds.filer_by_report,
                  late_12m=sum(1 for r in year_ago if r.late), reports_12m=len(year_ago),
                  late_trades_12m=sum(1 for r in year_ago for t in r.trades if t.late))

    # ---- data files, feed, sitemap

    def build_data_files(self):
        ds = self.ds
        search = []
        for filer in sorted(ds.filers.values(), key=lambda f: f.name):
            if filer.trades:
                search.append({"t": "member", "n": filer.name, "s": filer.party_state or filer.filer_type,
                               "u": f"/congress/{filer.slug}/"})
        for stock in sorted((s for s in ds.stocks.values() if s["count"]), key=lambda s: -s["count"]):
            search.append({"t": "stock", "n": stock["ticker"], "k": stock["name"], "s": stock["name"],
                           "u": f"/stocks/{stock['slug']}/"})
        self.write("/data/search.json", json.dumps(search, ensure_ascii=False, separators=(",", ":")))
        by_year = defaultdict(list)
        for t in ds.trades_sorted():
            if t.received:
                by_year[t.received.year].append(trade_record(t))
        for year, rows in by_year.items():
            self.write(f"/data/trades-{year}.json", json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
        self.write("/data/years.json", json.dumps({str(y): len(r) for y, r in sorted(by_year.items(), reverse=True)}))

    def build_feed(self):
        ds = self.ds
        trades_by_report = defaultdict(list)
        for t in ds.trades:
            trades_by_report[t.report_path].append(t)
        reports = [r for r in ds.reports if r.received and r.path in trades_by_report]
        reports.sort(key=lambda r: (r.received, r.first_seen or datetime.min), reverse=True)
        entries = []
        for report in reports[:FEED_ENTRIES]:
            filer = ds.filer_by_report.get(report.path)
            trades = sorted(trades_by_report[report.path], key=lambda t: t.row)
            who = filer.name if filer else report.filer_name
            extra = f" ({filer.party_state})" if filer and filer.party_state else ""
            items = "".join(
                f"<li>{escape(t.label)}: {escape(t.ticker_text if t.is_stock else (t.asset_name or 'asset'))}"
                f"{' (' + escape(t.asset_name) + ')' if t.is_stock and t.asset_name else ''}, "
                f"{escape(t.amount or 'amount not given')}, traded {sitedata.fmt_date(t.traded) or 'date not given'}"
                f"{', late' if t.late else ''}</li>" for t in trades)
            content = (f"<p>{escape(who)}{escape(extra)} disclosed {len(trades)} trade"
                       f"{'s' if len(trades) != 1 else ''} on {sitedata.fmt_date(report.received)}"
                       f"{' (amendment)' if report.is_amendment else ''}.</p><ul>{items}</ul>"
                       f"<p><a href=\"{escape(report.url)}\">Official report</a></p>")
            entries.append({
                "title": f"{who}: {len(trades)} trade{'s' if len(trades) != 1 else ''} disclosed "
                         f"{sitedata.fmt_date(report.received)}",
                "link": f"{self.site_url}/congress/{filer.slug}/" if filer else report.url,
                "id": f"{self.site_url}/reports/{report.path.strip('/').rsplit('/', 1)[-1]}",
                "updated": rfc3339(report.first_seen or report.received),
                "published": rfc3339(report.received),
                "content": content,
            })
        feed_updated = entries[0]["updated"] if entries else rfc3339(datetime.now(timezone.utc).replace(tzinfo=None))
        xml = self.env.get_template("feed.xml").render(entries=entries, feed_updated=feed_updated)
        self.write("/feed.xml", xml)

    def build_sitemap(self):
        today = date.today().isoformat()
        urls = "".join(f"<url><loc>{escape(self.site_url + p)}</loc><lastmod>{today}</lastmod></url>"
                       for p in self.pages)
        self.write("/sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>'
                   '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + "</urlset>")
        self.write("/robots.txt", f"User-agent: *\nAllow: /\nSitemap: {self.site_url}/sitemap.xml\n")


# ---------------------------------------------------------------------- main

def connect_from_env():
    import database
    url = (os.environ.get("SUPABASE_DB_URL") or "").strip()
    password = os.environ.get("SUPABASE_DB_PASSWORD") or ""
    if not url:
        raise database.DatabaseError("SUPABASE_DB_URL is not set")
    return database.connect(url, password)


def main(argv=None, conn=None, legislators=None):
    parser = argparse.ArgumentParser(description="Build the Insider Tracking website.")
    parser.add_argument("--out", default="_site", help="output folder (default: _site)")
    parser.add_argument("--base", help="base path on the domain, e.g. /insider-tracking")
    parser.add_argument("--site-url", help="full address of the site")
    parser.add_argument("--offline", action="store_true", help="don't download the legislators list")
    args = parser.parse_args(argv)

    repo, base, site_url = site_defaults()
    base = args.base.rstrip("/") if args.base is not None else base
    site_url = args.site_url.rstrip("/") if args.site_url else site_url

    log(f"Insider Tracking site build -> {args.out} (base path {base or '/'}, {site_url})")
    started = time.time()
    close = None
    if conn is None:
        conn = connect_from_env()
        close = conn.close
    try:
        log("Reading the database…")
        dataset = sitedata.load(conn)
    finally:
        if close:
            close()
    log(f"  {len(dataset.reports)} reports, {len(dataset.trades)} trades, "
        f"{len(dataset.filers)} filers; data updated {dataset.updated or 'never'}")

    log("Matching names to the legislators list…")
    if legislators is None:
        legislators = people.load_legislators(HERE / "cache", offline=args.offline, log=log)
    matcher = people.Matcher(legislators, people.load_overrides(HERE / "overrides.json"), log=log)
    dataset.apply_people(matcher)
    log(f"  {matcher.matched} filers matched")
    if matcher.unmatched:
        log(f"  {len(matcher.unmatched)} members of Congress without party/state "
            "(add them to site/overrides.json):")
        for filer in matcher.unmatched:
            log(f"    {filer.key!r}: {filer.match_note}")

    log("Writing pages…")
    Builder(dataset, args.out, base, site_url, repo).build()
    log(f"Done in {time.time() - started:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
