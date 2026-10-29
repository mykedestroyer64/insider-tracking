"""
insiders.py - the Insiders pages: one for all markets and one per market (/insiders/sweden/),
one page per insider, and the per-letter name index the Insiders page's search loads.
"""

import json
from collections import defaultdict
from datetime import date

from .. import charts
from ..returns.rules import summary


class InsiderPages:
    def build(self, site):
        ins = site.insiders
        # one page for all markets, and one per market (/insiders/sweden/ ...)
        pages = [(None, ins.market_info(None))] + [(market.code, market) for market in ins.markets]
        for country, market in pages:
            path = "/insiders/index.html" if country is None else f"/insiders/{market.slug}/index.html"
            weekly = ins.weekly_counts(country=country)
            site.writer.page("insiders.html", path, page="insiders", market=market, markets=ins.markets,
                             all_markets=country is None,
                             stats=ins.overview(country=country), latest=ins.latest(country=country),
                             biggest_buys=ins.biggest("buy", country=country), biggest_sells=ins.biggest("sell", country=country),
                             most_active=ins.most_active(country=country), weekly=weekly,
                             weekly_svg=charts.buys_sells_svg(weekly) if ins.counted_trades(country) else "",
                             people=ins.people)
        for person in ins.people.values():
            trades = sorted(person.trades, key=lambda t: (t.filing.filed or date.min, t.traded or date.min), reverse=True)
            site.writer.page("insider.html", f"/insiders/{person.slug}/index.html", page="insiders", person=person,
                             stats=person.stats(), trades=trades,
                             perf_summary=summary(person.counted, lambda t: t.kind))
        # per-letter name index for the Insiders page search
        by_letter = defaultdict(list)
        for person in sorted(ins.people.values(), key=lambda p: p.name):
            words = person.name.split()
            for word in {words[-1], words[0]} if words else set():
                first = word[:1].lower()
                if first.isalpha():
                    by_letter[first].append({"n": person.name, "s": person.description[:80], "u": f"/insiders/{person.slug}/"})
        for letter, rows in by_letter.items():
            unique = {r["u"]: r for r in rows}
            site.writer.write(f"/data/insiders-{letter}.json", json.dumps(list(unique.values()), ensure_ascii=False, separators=(",", ":")))
