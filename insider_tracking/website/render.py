"""
render.py - the templates (site/templates) and everything they can use.

The pages are Jinja templates; what they show comes from the page builders (pages/), and the
helpers and settings every page shares are the templates' "globals", listed here.
"""

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import formatting
from .congress.presentation import days_text, headline, home_trade_json, short_asset
from .returns.models import LABELS, PERIODS, SHORT

NAV = [
    ("home", "/", "Overview", '<rect x="2" y="2" width="5" height="5"></rect><rect x="9" y="2" width="5" height="5"></rect><rect x="2" y="9" width="5" height="5"></rect><rect x="9" y="9" width="5" height="5"></rect>'),
    ("trades", "/trades/", "Trades", '<path d="M2 5.5h10M9.5 3l2.5 2.5L9.5 8"></path><path d="M14 10.5H4M6.5 8L4 10.5 6.5 13"></path>'),
    ("congress", "/congress/", "Congress", '<circle cx="8" cy="5.5" r="3"></circle><path d="M2.5 14c.6-3 2.7-4.5 5.5-4.5s4.9 1.5 5.5 4.5"></path>'),
    ("stocks", "/stocks/", "Stocks", '<path d="M2 13.5l3.5-4 3 2.5L14 5"></path><path d="M10.5 5H14v3.5"></path>'),
    ("insiders", "/insiders/", "Insiders", '<path d="M3 14V3.5A1.5 1.5 0 0 1 4.5 2h5A1.5 1.5 0 0 1 11 3.5V14M11 7h2a1 1 0 0 1 1 1v6M2 14h13"></path><path d="M5.5 5h3M5.5 8h3M5.5 11h3"></path>'),
    ("portfolios", "/portfolios/", "Portfolios", '<rect x="2" y="5" width="12" height="8.5" rx="1"></rect><path d="M5.5 5V3.5A1.5 1.5 0 0 1 7 2h2a1.5 1.5 0 0 1 1.5 1.5V5M2 9h12"></path>'),
    ("late", "/late/", "Late filings", '<circle cx="8" cy="8" r="6.5"></circle><path d="M8 4.5V8l2.5 1.5"></path>'),
    ("alerts", "/alerts/", "Alerts", '<path d="M4 11V7.5a4 4 0 0 1 8 0V11l1 1.5H3L4 11z"></path><path d="M6.5 14h3"></path>'),
    ("about", "/about/", "About", '<circle cx="8" cy="8" r="6.5"></circle><path d="M8 7v4M8 4.8v.4"></path>'),
]
ICON = '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" class="icon">{}</svg>'


def template_globals(config, congress, insiders, build_id):
    """What every page can use: the site's settings, the navigation, the formatting helpers,
    and whether there are insiders and returns to show."""
    updated = congress.updated
    return {
        "base": config.base, "site_url": config.url, "repo": config.repo,
        "build_id": build_id,
        "updated": f"{updated:%b} {updated.day}, {updated:%Y} {updated:%H:%M} UTC" if updated else "",
        "nav": [{"id": i, "href": h, "label": l, "icon": ICON.format(svg)} for i, h, l, svg in NAV],
        "fmt_date": formatting.fmt_date, "fmt_number": formatting.fmt_number,
        "headline": headline, "days_text": days_text, "trade_json": home_trade_json,
        "short_asset": short_asset, "fmt_money": formatting.fmt_money,
        "fmt_money_short": formatting.fmt_money_short, "fmt_shares": formatting.fmt_shares,
        "fmt_price": formatting.fmt_price, "fmt_eur_short": formatting.fmt_eur_short,
        "has_insiders": bool(insiders.trades), "insider_markets": insiders.markets,
        "fmt_pct": formatting.fmt_pct, "periods": PERIODS,
        "period_label": LABELS, "period_short": SHORT,
        "has_returns": any(t.perf for t in congress.trades) or any(t.perf for t in insiders.trades),
    }


class Renderer:
    def __init__(self, templates_folder, globals_):
        self.env = Environment(loader=FileSystemLoader(str(templates_folder)),
                               autoescape=select_autoescape(["html", "xml"]),
                               trim_blocks=True, lstrip_blocks=True)
        self.env.globals.update(globals_)

    def render(self, template, **context):
        return self.env.get_template(template).render(**context)
