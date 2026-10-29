"""describe.py - short, readable descriptions of web answers and network errors."""

import html
import re
from urllib.parse import urlparse

import requests


def http_codes(response):
    """Status codes of a response and of any redirects before it, e.g. [302, 200]."""
    if response is None:
        return []
    return [r.status_code for r in response.history] + [response.status_code]


def path_of(url):
    """The part of an address after the host: /search/home/."""
    return urlparse(url).path


def short_path(url, limit=90):
    """The path of an address, cut to `limit` characters: /Archives/edgar/daily-index/..."""
    path = urlparse(url or "").path or "/"
    return path if len(path) <= limit else path[:limit - 3] + "..."


def short_error(error):
    """A short, readable description of a network error."""
    if isinstance(error, requests.Timeout):
        return "timed out"
    if isinstance(error, requests.exceptions.SSLError):
        return "secure connection failed"
    if isinstance(error, requests.ConnectionError):
        text = str(error).lower()
        if any(hint in text for hint in ("getaddrinfo", "name or service", "nodename",
                                         "name resolution")):
            return "the site's address could not be looked up"
        if "refused" in text:
            return "connection refused"
        if "reset" in text or "aborted" in text:
            return "connection cut off"
        return "could not connect"
    return " ".join(f"{type(error).__name__}: {error}".split())[:160]


def page_says(response, length=160):
    """'; the page says: "..."' with the first words of an HTML page, or '' for other answers."""
    if "html" not in response.headers.get("Content-Type", "") and "<html" not in response.text[:500].lower():
        return ""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", response.text[:20000], flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    return f"; the page says: \"{text[:length]}\"" if text else ""
