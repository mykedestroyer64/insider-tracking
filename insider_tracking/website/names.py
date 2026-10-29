"""names.py - names as the pages show them, and the page addresses (slugs) made from them."""

import re
import unicodedata


def squash(text):
    """Lower-case letters only, accents removed: 'McConnell, Jr.' -> 'mcconnelljr'."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text.lower() if ch.isalnum())


def slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "unknown"


def display_name(first, last):
    """eFD stores 'A. Mitchell' + 'McConnell, Jr.'; show 'A. Mitchell McConnell Jr.'."""
    last_core, _, suffix = (last or "").partition(",")
    name = f"{(first or '').strip()} {last_core.strip()}".strip()
    if suffix.strip():
        name += f" {suffix.strip()}"
    return name


def last_name_core(last):
    """The family name without a suffix: 'McConnell, Jr.' -> 'McConnell'."""
    return (last or "").partition(",")[0].strip()


def stock_slug(ticker):
    """The stock page's address for a ticker: 'BRK.B' -> 'BRK.B' (None when there's no ticker)."""
    return re.sub(r"[^A-Z0-9.\-]", "", (ticker or "").upper()) or None


def free_slug(base, used):
    """`base`, or base-2, base-3, ... if it is taken already; the slug returned is marked as taken."""
    slug, n = base, 2
    while slug in used:
        slug = f"{base}-{n}"
        n += 1
    used.add(slug)
    return slug
