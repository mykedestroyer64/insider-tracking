"""trades.py - the Trades page (its table loads the yearly data files, see data_files.py)."""


class TradesPage:
    def build(self, site):
        ds = site.congress
        filers = sorted((f for f in ds.filers.values() if f.trades), key=lambda f: f.name)
        site.writer.page("trades.html", "/trades/index.html", page="trades", years=ds.years(), filers=filers)
