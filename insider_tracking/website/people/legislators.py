"""
legislators.py - the public-domain congress-legislators dataset (the @unitedstates project):
every member of Congress with their terms, party and state.

The two files (current and historical members) are downloaded once a week and cached. If they
can't be downloaded and there is no cache, the site is still built, just without party and state.
"""

import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

from ..names import squash

SOURCES = [
    "https://unitedstates.github.io/congress-legislators/legislators-current.json",
    "https://unitedstates.github.io/congress-legislators/legislators-historical.json",
]
CACHE_DAYS = 7
SINCE = date(2011, 1, 1)      # eFD starts in 2012; keep people who served from 2011 on
PARTY_LETTERS = {"Democrat": "D", "Republican": "R", "Independent": "I"}


class Legislator:
    def __init__(self, record, today=None):
        self.today = today or date.today()      # whether someone still serves depends on the day
        name = record.get("name", {})
        self.bioguide = record.get("id", {}).get("bioguide")
        self.first = name.get("first", "") or ""
        self.last = name.get("last", "") or ""
        self.nickname = name.get("nickname", "") or ""
        self.suffix = name.get("suffix", "") or ""
        self.official = name.get("official_full") or f"{self.first} {self.last}".strip()
        terms = record.get("terms", [])
        self.sen_terms = [t for t in terms if t.get("type") == "sen"]
        self.rep_terms = [t for t in terms if t.get("type") == "rep"]
        self.terms = self.sen_terms + self.rep_terms
        self.last_term = max(self.terms, key=lambda t: t.get("end", ""), default=None)   # latest seat

    @property
    def served_recently(self):
        return self.last_term is not None and self.last_term.get("end", "") >= SINCE.isoformat()

    def served_in(self, chamber):
        """Served in this chamber ("senate" or "house") since 2011."""
        terms = self.sen_terms if chamber == "senate" else self.rep_terms
        return any(t.get("end", "") >= SINCE.isoformat() for t in terms)

    @property
    def serving(self):
        return (self.last_term or {}).get("end", "") >= self.today.isoformat()

    @property
    def role(self):
        """Senator, Former Senator, Representative or Former Representative (their latest seat)."""
        kind = "Senator" if (self.last_term or {}).get("type") == "sen" else "Representative"
        return kind if self.serving else f"Former {kind}"

    @property
    def district(self):
        """The latest House seat as the House writes it (CA11; 00 = at large), if the latest seat is there."""
        term = self.last_term or {}
        if term.get("type") != "rep" or not term.get("state"):
            return ""
        try:
            return f"{term['state']}{int(term.get('district') or 0):02d}"
        except (TypeError, ValueError):
            return ""

    @property
    def party(self):
        party = (self.last_term or {}).get("party", "")
        return PARTY_LETTERS.get(party, party[:1] if party else None)

    @property
    def state(self):
        return (self.last_term or {}).get("state")

    @property
    def first_tokens(self):
        tokens = set()
        for text in (self.first, self.nickname, self.official.split(" ")[0] if self.official else ""):
            for token in text.replace("-", " ").split():
                tokens.add(squash(token))
        return {t for t in tokens if t}


def load_legislators(cache_dir, offline=False, log=print, today=None):
    """Download (or read from cache) the current and historical legislators."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for url in SOURCES:
        path = cache_dir / url.rsplit("/", 1)[-1]
        fresh = path.exists() and (time.time() - path.stat().st_mtime) < CACHE_DAYS * 86400
        if not fresh and not offline:
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "insider-tracking site build"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = response.read()
                json.loads(data)                    # make sure it's valid before caching
                path.write_bytes(data)
                log(f"  downloaded {path.name} ({len(data) // 1024} KB)")
            except Exception as error:              # noqa: BLE001 - any download problem
                log(f"  could not download {url}: {error}")
        if path.exists():
            try:
                records += json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                log(f"  cached {path.name} is not valid JSON, ignoring it")
        else:
            log(f"  no copy of {path.name}: party and state will be missing for some senators")
    return [Legislator(r, today) for r in records]


def load_overrides(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        print(f"  overrides file {path} is not valid JSON ({error}); ignoring it", file=sys.stderr)
        return {}
