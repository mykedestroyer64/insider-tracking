"""formatting.py - numbers, money, dates and percentages as the pages show them."""

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def fmt_date(value):
    """'Oct 5, 2026', or '' when unknown."""
    return f"{value:%b} {value.day}, {value:%Y}" if value else ""


def fmt_number(value):
    return f"{value:,}" if value is not None else ""


def money_prefix(currency):
    symbol = SYMBOLS.get(currency or "USD")
    return symbol if symbol else f"{currency} "      # "SEK 1,234" stays on one line


def fmt_money(value, currency="USD"):
    """$1,234,567 or SEK 1,234,567 (whole units), or '–' when unknown."""
    if value is None:
        return "–"
    return f"{money_prefix(currency)}{value:,.0f}"


def fmt_money_short(value, currency="USD"):
    """$1.2M, SEK 340K, €950: for tiles and tight columns ('–' when nothing)."""
    if not value:
        return "–"
    prefix = money_prefix(currency)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            number = value / limit
            return f"{prefix}{number:,.1f}{suffix}" if number < 100 else f"{prefix}{number:,.0f}{suffix}"
    return f"{prefix}{value:,.0f}"


def fmt_eur_short(value):
    return "≈ " + fmt_money_short(value, "EUR") if value else ""


def fmt_shares(value):
    if value is None:
        return "–"
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def fmt_price(value, currency="USD"):
    if value is None:
        return "–"
    prefix = money_prefix(currency)
    return f"{prefix}{value:,.2f}" if value >= 1 else f"{prefix}{value:,.4f}".rstrip("0").rstrip(".")


def fmt_pct(value, signed=True):
    """+12.4% / −3.1% (a real minus sign), or – when unknown."""
    if value is None:
        return "–"
    percent = value * 100
    if signed and round(percent, 1) > 0:
        return f"+{percent:,.1f}%"
    if round(percent, 1) < 0:
        return f"−{abs(percent):,.1f}%"
    return "0.0%"
