"""home.py - the Overview page: the latest trades, the activity charts and the filing delays."""

import json

from .. import charts

LATEST_ON_HOME = 12


class HomePage:
    def build(self, site):
        ds = site.congress
        activity = charts.activity_charts(ds)
        latest = [t for t in ds.trades_sorted() if t.counted][:LATEST_ON_HOME]
        site.writer.page("index.html", "/index.html", page="home",
                         stats=ds.overview_stats(), charts=activity,
                         charts_json=json.dumps(activity, separators=(",", ":")),
                         histogram_svg=charts.histogram_svg(ds.delay_histogram()), latest=latest,
                         insider_latest=site.insiders.latest(8), insider_stats=site.insiders.overview())
