"""
One builder per group of pages. Each is a PageBuilder: build(site) writes its pages, where
`site` is a SiteContext: the data (Congress, insiders), the writer and the site's settings.
PAGES is the order they are built in (it is also the order of the sitemap). A new page is a new
builder in this list; nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

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

if TYPE_CHECKING:
    from ..config import SiteConfig
    from ..congress.data import CongressData
    from ..insiders.data import InsiderData
    from ..writer import SiteWriter


@dataclass
class SiteContext:
    congress: CongressData
    insiders: InsiderData
    writer: SiteWriter
    config: SiteConfig
    now: datetime         # when the build runs (UTC)
    today: date


@runtime_checkable
class PageBuilder(Protocol):
    def build(self, site: SiteContext) -> None: ...


PAGES: tuple[PageBuilder, ...] = (HomePage(), TradesPage(), CongressPages(), StockPages(), InsiderPages(), LatePage(),
                                  SimplePages(), DataFiles(), Feed(), Sitemap())
