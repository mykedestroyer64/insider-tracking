"""
presentation.py - how a Congress trade is worded on the pages: the headline, the short asset
type, the days it took to disclose, and the compact record the Trades page loads.
"""

import json

from .models import LATE_AFTER_DAYS


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
        return f"{trade.days}, late ({trade.days - LATE_AFTER_DAYS} days past the 45-day limit)"
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
