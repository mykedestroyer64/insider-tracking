"""
matcher.py - adds party, state and official names to the people who file reports.

eFD only gives names ("Fetterman, John (Senator)"); the House gives names plus state and
district ("CA11"). Senate filers are matched to senators by last name plus first name; House
filers to representatives by last name, state and first name (and district if needed).
Anything ambiguous is left unmatched and listed in the build log, so it can be fixed by hand
in site/overrides.json, like this:
    {"senate|McConnell, Jr.|A. Mitchell": "M000355",
     "house|Pelosi|Nancy|CA": "P000197",
     "house|Reisdorf|Richard B.|MN": "candidate"}
(the key is the filer's key as the build log prints it, the value a bioguide ID, or "candidate"
for someone who files trade reports while running for Congress but has never been a member:
they are listed with the candidates, and the build log stops asking about them).
"""

import re

from ..names import last_name_core, slugify, squash

CANDIDATE = "candidate"     # the override for someone who ran for Congress but was never a member

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
        if override == CANDIDATE:
            filer.filer_type = "Candidate"
            filer.match_note = "a candidate, never a member (site/overrides.json)"
            return
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
