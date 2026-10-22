"""
people.py - adds party and state to the people who file reports.

eFD only gives names ("Fetterman, John (Senator)"). Party and state come from
the public-domain congress-legislators dataset maintained by the
@unitedstates project. The two files (current and historical members) are
downloaded once a week and cached. If they can't be downloaded and there is
no cache, the site is still built, just without party and state.

Matching is by last name plus first name. Anything ambiguous is left
unmatched and listed in the build log, so it can be fixed by hand in
site/overrides.json, like this:
    {"McConnell, Jr.|A. Mitchell": "M000355"}
(the key is the filer's "last|first" exactly as eFD writes it, the value a
bioguide ID).
"""

import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

from data import last_name_core, slugify, squash

SOURCES = [
    "https://unitedstates.github.io/congress-legislators/legislators-current.json",
    "https://unitedstates.github.io/congress-legislators/legislators-historical.json",
]
CACHE_DAYS = 7
SINCE = date(2011, 1, 1)      # eFD starts in 2012; keep people who served from 2011 on
PARTY_LETTERS = {"Democrat": "D", "Republican": "R", "Independent": "I"}


class Legislator:
    def __init__(self, record):
        name = record.get("name", {})
        self.bioguide = record.get("id", {}).get("bioguide")
        self.first = name.get("first", "") or ""
        self.last = name.get("last", "") or ""
        self.nickname = name.get("nickname", "") or ""
        self.suffix = name.get("suffix", "") or ""
        self.official = name.get("official_full") or f"{self.first} {self.last}".strip()
        self.terms = [t for t in record.get("terms", []) if t.get("type") == "sen"]
        self.last_term = max(self.terms, key=lambda t: t.get("end", ""), default=None)

    @property
    def served_recently(self):
        return self.last_term is not None and self.last_term.get("end", "") >= SINCE.isoformat()

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


class Matcher:
    def __init__(self, legislators, overrides=None, log=print):
        self.by_bioguide = {l.bioguide: l for l in legislators if l.bioguide}
        self.by_last = {}
        for leg in legislators:
            if leg.terms and leg.served_recently:
                self.by_last.setdefault(squash(leg.last), []).append(leg)
        self.overrides = overrides or {}
        self.log = log
        self.unmatched = []
        self.matched = 0

    def apply(self, filer):
        leg = None
        override = self.overrides.get(filer.key)
        if override:
            leg = self.by_bioguide.get(override)
            if leg is None:
                self.log(f"  override for {filer.key!r} points to unknown bioguide {override!r}")
        if leg is None:
            leg, note = self.find(filer)
            filer.match_note = note
        if leg is None:
            if filer.is_senator:          # candidates are not expected in the list
                self.unmatched.append(filer)
            return
        self.matched += 1
        filer.bioguide = leg.bioguide
        filer.party = leg.party
        filer.state = leg.state
        filer.name = leg.official
        filer.slug = slugify(leg.official)

    def find(self, filer):
        last = squash(last_name_core(filer.last_name))
        candidates = self.by_last.get(last, [])
        if not candidates:
            return None, "no senator with this last name"
        first_tokens = {squash(t) for t in filer.first_name.replace("-", " ").split()}
        first_tokens = {t for t in first_tokens if t}

        def compatible(leg):
            for mine in first_tokens:
                for theirs in leg.first_tokens:
                    if mine == theirs or (len(mine) > 1 and len(theirs) > 1
                                          and (mine.startswith(theirs) or theirs.startswith(mine))):
                        return True
            return False

        good = [leg for leg in candidates if compatible(leg)]
        if len(good) == 1:
            return good[0], "matched by last and first name"
        if not good and len(candidates) == 1:
            return candidates[0], "matched by last name only (first name differs)"
        if len(good) > 1:
            initials = [leg for leg in good if any(t[0] == squash(filer.first_name)[:1] for t in leg.first_tokens)]
            if len(initials) == 1:
                return initials[0], "matched by last name and first initial"
        return None, f"ambiguous: {len(candidates)} senators share this last name"


def load_legislators(cache_dir, offline=False, log=print):
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
    return [Legislator(r) for r in records]


def load_overrides(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        print(f"  overrides file {path} is not valid JSON ({error}); ignoring it", file=sys.stderr)
        return {}
