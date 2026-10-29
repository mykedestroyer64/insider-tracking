"""
congress.py - the Congress page (every member, and the candidates) and one page per member,
with their trades, the stocks they trade most and how those stocks did afterwards.
"""

from collections import Counter, defaultdict

from .. import charts
from ..returns.rules import summary

TOP_STOCKS_ON_MEMBER_PAGE = 10


class CongressPages:
    def build(self, site):
        ds = site.congress
        with_stats = [(f, f.stats()) for f in ds.filers.values()]
        members = sorted((x for x in with_stats if x[0].is_member), key=lambda x: (-x[1]["trades"], x[0].name))
        others = sorted((x for x in with_stats if not x[0].is_member), key=lambda x: (-x[1]["trades"], x[0].name))
        site.writer.page("congress.html", "/congress/index.html", page="congress",
                         members=members, candidates=others,
                         senate_count=sum(1 for f, _ in members if not f.in_house),
                         house_count=sum(1 for f, _ in members if f.in_house))
        # The pages used to live under /senators/: keep those links working
        site.writer.redirect("/senators/index.html", "/congress/")
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
            site.writer.page("member.html", f"/congress/{filer.slug}/index.html", page="congress",
                             filer=filer, stats=stats, top_stocks=top,
                             perf_summary=summary(filer.counted_trades, lambda t: t.group),
                             chambers=sorted({r.chamber for r in filer.reports}),
                             yearly_svg=charts.yearly_bars_svg(by_year, filer.name) if len(by_year) > 1 else "")
            if any(r.chamber == "senate" for r in filer.reports):
                site.writer.redirect(f"/senators/{filer.slug}/index.html", f"/congress/{filer.slug}/")
