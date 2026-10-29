"""
One builder per group of pages. Each has build(site), where `site` is a SiteContext: the
data (Congress, insiders), the writer and the site's settings. PAGES is the order they are
built in (it is also the order of the sitemap). A new page is a new builder in this list.
"""

from dataclasses import dataclass
from datetime import date, datetime

from .congress import CongressPages
from .data_files import DataFiles
from .feed import Feed
from .home import HomePage
from .insiders import InsiderPages
from .late import LatePage
from .simple import SimplePages
from .sitemap import Sitemap
from .stocks import StockPages
from .trades import TradesPage


@dataclass
class SiteContext:
    congress: object      # website.congress.data.CongressData
    insiders: object      # website.insiders.data.InsiderData
    writer: object        # website.writer.SiteWriter
    config: object        # website.config.SiteConfig
    now: datetime         # when the build runs (UTC)
    today: date


PAGES = (HomePage(), TradesPage(), CongressPages(), StockPages(), InsiderPages(), LatePage(), SimplePages(),
         DataFiles(), Feed(), Sitemap())
