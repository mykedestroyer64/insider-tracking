"""
people.py - adds party, state and official names to the people who file reports.

eFD only gives names ("Fetterman, John (Senator)"); the House gives names plus
state and district ("CA11"). Party and state come from the public-domain
congress-legislators dataset maintained by the @unitedstates project. The two
files (current and historical members) are downloaded once a week and cached.
If they can't be downloaded and there is no cache, the site is still built,
just without party and state.

Senate filers are matched to senators by last name plus first name; House
filers to representatives by last name, state and first name (and district if
needed). Anything ambiguous is left unmatched and listed in the build log, so
it can be fixed by hand in site/overrides.json, like this:
    {"senate|McConnell, Jr.|A. Mitchell": "M000355",
     "house|Pelosi|Nancy|CA": "P000197"}
(the key is the filer's key as the build log prints it, the value a bioguide ID).
"""

import json
import re
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

# First names eFD and the legislators list may write differently ("Thomas" vs "Tom").
NICKNAME_GROUPS = """
thomas tom tommy | william bill billy will | robert rob bob bobby | richard rick dick rich
james jim jimmy | john jack johnny | michael mike | joseph joe joey | christopher chris
edward ed ted eddie ned | theodore ted | charles chuck charlie | patrick pat | patricia pat patty tricia
benjamin ben | timothy tim | daniel dan danny | jonathan jon | margaret maggie peggy
elizabeth liz beth betsy | catherine katherine kate katie cathy kathy | anthony tony
andrew andy drew | matthew matt | steven stephen steve | kenneth ken | ronald ron
donald don | gerald jerry | jerome jerry | lawrence larry | raymond ray | samuel sam
alexander alex | nicholas nick | gregory greg | jeffrey jeff | frederick fred | peter pete
deborah debra deb debbie | susan sue | jacqueline jacklyn jacky jackie | kimberly kim
cynthia cindy | bernard bernie | joshua josh | martin marty | mitchell mitch | douglas doug
rebecca becky | jennifer jen jenny | victoria vicky | joan joanne | raphael rafael
"""
NICKNAMES = {}
for _group in NICKNAME_GROUPS.replace("\n", "|").split("|"):
    _names = _group.split()
    for _name in _names:
        NICKNAMES.setdefault(_name, set()).update(_names)


def same_first_name(mine, theirs):
    """'thomas' and 'tom', 'rob' and 'robert', 'jd' and 'jd'... (both already squashed)."""
    if mine == theirs:
        return True
    if len(mine) > 1 and len(theirs) > 1 and (mine.startswith(theirs) or theirs.startswith(mine)):
        return True
    return theirs in NICKNAMES.get(mine, ())


TITLES = {"md", "facs", "phd", "jd", "dds", "esq", "jr", "sr", "ii", "iii", "iv"}


def name_words(name):
    """The words of a last name that can identify someone (no titles like MD or Jr.)."""
    words = [squash(word) for word in re.split(r"[\s\-]+", name or "")]
    return [word for word in words if len(word) >= 3 and word not in TITLES]


class Legislator:
    def __init__(self, record):
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
        return (self.last_term or {}).get("end", "") >= date.today().isoformat()

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


class Matcher:
    def __init__(self, legislators, overrides=None, log=print):
        self.by_bioguide = {l.bioguide: l for l in legislators if l.bioguide}
        self.by_last = {"senate": {}, "house": {}}   # last name -> people who served in that chamber
        self.by_word = {"senate": {}, "house": {}}   # each word of a last name ("McMorris Rodgers")
        for leg in legislators:
            for chamber in ("senate", "house"):
                if leg.served_in(chamber):
                    self.by_last[chamber].setdefault(squash(leg.last), []).append(leg)
                    for word in name_words(leg.last):
                        self.by_word[chamber].setdefault(word, []).append(leg)
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
            if filer.is_member:           # candidates are not expected in the list
                self.unmatched.append(filer)
                if filer.chamber == "house":
                    filer.filer_type = "House filer"   # not found among representatives: listed apart
            return
        self.matched += 1
        filer.bioguide = leg.bioguide
        filer.party = leg.party
        filer.state = leg.state
        filer.name = leg.official
        filer.slug = slugify(leg.official)
        if filer.chamber == "house" or not filer.filer_type:
            # The House doesn't say whether someone still serves (and eFD leaves the type out of
            # some paper filings): use their latest seat, which may now be in the Senate.
            filer.filer_type = leg.role
        if leg.district:
            filer.district = leg.district

    def find(self, filer):
        chamber = "house" if filer.chamber == "house" else "senate"
        people = "representatives" if chamber == "house" else "senators"
        last = squash(last_name_core(filer.last_name))
        candidates = self.by_last[chamber].get(last, [])
        if not candidates:
            # Last names written differently: "Rodgers" for McMorris Rodgers, "Paulina Luna" for
            # Luna, "Dunn MD, FACS" for Dunn. Try each word of the name.
            found = {}
            for word in name_words(last_name_core(filer.last_name)):
                for leg in self.by_word[chamber].get(word, []):
                    found[leg.bioguide or id(leg)] = leg
            candidates = list(found.values())
        if not candidates:
            return None, f"no {people[:-1]} with this last name"
        state = (filer.district or "")[:2] if chamber == "house" else ""
        if state:   # the House gives the state: keep those who served for it, if any did
            in_state = [leg for leg in candidates if any(t.get("state") == state for t in leg.rep_terms)]
            candidates = in_state or candidates
        first_tokens = {squash(t) for t in filer.first_name.replace("-", " ").split()}
        first_tokens = {t for t in first_tokens if t}

        def compatible(leg):
            return any(same_first_name(mine, theirs) for mine in first_tokens for theirs in leg.first_tokens)

        good = [leg for leg in candidates if compatible(leg)]
        if len(good) == 1:
            return good[0], "matched by last and first name"
        if not good and len(candidates) == 1:
            return candidates[0], "matched by last name only (first name differs)"
        if len(good) > 1:
            initials = [leg for leg in good if any(t[0] == squash(filer.first_name)[:1] for t in leg.first_tokens)]
            if len(initials) == 1:
                return initials[0], "matched by last name and first initial"
            if state and filer.district[2:].isdigit():
                district = int(filer.district[2:])
                same_seat = [leg for leg in good if any(t.get("state") == state and int(t.get("district") or 0) == district
                                                        for t in leg.rep_terms)]
                if len(same_seat) == 1:
                    return same_seat[0], "matched by last name, first name and district"
        return None, f"ambiguous: {len(candidates)} {people} share this last name"


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
