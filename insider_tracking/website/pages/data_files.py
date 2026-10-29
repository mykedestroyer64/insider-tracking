"""
data_files.py - the JSON files the pages load: the search box's list (members, stocks and
companies), and the Trades page's trades, one file per year.
"""

import json
from collections import defaultdict

from ..congress.presentation import trade_record


class DataFiles:
    def build(self, site):
        ds = site.congress
        search = []
        for filer in sorted(ds.filers.values(), key=lambda f: f.name):
            if filer.trades:
                search.append({"t": "member", "n": filer.name, "s": filer.party_state or filer.filer_type,
                               "u": f"/congress/{filer.slug}/"})
        for stock in sorted((s for s in ds.stocks.values() if s["count"]), key=lambda s: -s["count"]):
            search.append({"t": "stock", "n": stock["ticker"], "k": stock["name"], "s": stock["name"],
                           "u": f"/stocks/{stock['slug']}/"})
        listed = {s["slug"] for s in ds.stocks.values() if s["count"]}
        for company in sorted(site.insiders.companies.values(), key=lambda c: -len(c.trades)):
            if company.slug not in listed:
                where = company.name if company.market.home else f"{company.name} · {company.market.name}"
                search.append({"t": "stock", "n": company.ticker, "k": company.name, "s": where,
                               "u": f"/stocks/{company.slug}/"})
        site.writer.write("/data/search.json", json.dumps(search, ensure_ascii=False, separators=(",", ":")))
        by_year = defaultdict(list)
        for t in ds.trades_sorted():
            if t.received:
                by_year[t.received.year].append(trade_record(t))
        for year, rows in by_year.items():
            site.writer.write(f"/data/trades-{year}.json", json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
        site.writer.write("/data/years.json", json.dumps({str(y): len(r) for y, r in sorted(by_year.items(), reverse=True)}))
