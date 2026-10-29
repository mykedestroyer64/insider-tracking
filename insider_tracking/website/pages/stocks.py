"""
stocks.py - the Stocks page and one page per stock: the members of Congress who traded it,
and (for companies with insiders) the insiders' trades, with a full list when there are many.
"""

from collections import Counter

from .. import charts
from ..insiders.data import ON_STOCK_PAGE


def insider_rows(trades):
    """Per-person summary of a company's insider trades (counted ones)."""
    rows = {}
    for t in trades:
        if not t.counted:
            continue
        row = rows.setdefault(t.insider_cik, {"cik": t.insider_cik, "name": t.insider_name, "title": t.role_text,
                                              "trades": 0, "buys": 0, "sells": 0, "bought": 0.0, "sold": 0.0, "last": None})
        row["trades"] += 1
        row["buys"] += t.kind == "buy"
        row["sells"] += t.kind == "sell"
        if t.kind == "buy":
            row["bought"] += t.value or 0
        else:
            row["sold"] += t.value or 0
        if t.traded and (row["last"] is None or t.traded > row["last"]):
            row["last"] = t.traded
    return sorted(rows.values(), key=lambda r: (-r["trades"], r["name"]))


class StockPages:
    def build(self, site):
        ds, ins = site.congress, site.insiders
        stocks = dict(ds.stocks)
        for slug, company in ins.companies.items():       # companies only insiders traded get a page too
            if slug not in stocks:                           # (European ones always: their slug is "se/name")
                stocks[slug] = {"ticker": company.ticker, "slug": slug, "name": company.name, "names": Counter(),
                                "trades": [], "count": 0, "filers": [], "buys": 0, "sells": 0, "last_received": None}
        for slug, stock in stocks.items():
            company = ins.companies.get(slug)
            stock["company"] = company
            stock["insider_stats"] = company.stats() if company else None
            if company and not ds.stocks.get(slug):
                stock["name"] = company.name
        listed = sorted((s for s in stocks.values() if s["count"]), key=lambda s: (-s["count"], s["ticker"]))
        site.writer.page("stocks.html", "/stocks/index.html", page="stocks", stocks=listed,
                         insider_only=sum(1 for s in stocks.values() if not s["count"]))
        for stock in stocks.values():
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
            company = stock["company"]
            insider_trades = company.trades if company else []
            per_insider = insider_rows(insider_trades)
            site.writer.page("stock.html", f"/stocks/{stock['slug']}/index.html", page="stocks", stock=stock,
                             by_filer=sorted(rows.values(), key=lambda r: (-r["trades"], r["filer"].name)),
                             yearly_svg=charts.yearly_bars_svg(by_year, stock["ticker"]) if len(by_year) > 1 else "",
                             insider_trades=insider_trades[:ON_STOCK_PAGE], insider_total=len(insider_trades),
                             insider_rows=per_insider, insider_people=site.insiders.people)
            if len(insider_trades) > ON_STOCK_PAGE:
                site.writer.page("stock_insiders.html", f"/stocks/{stock['slug']}/insiders/index.html", page="stocks",
                                 stock=stock, insider_trades=insider_trades, insider_people=site.insiders.people)
