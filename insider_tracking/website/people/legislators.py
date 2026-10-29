"""
legislators.py - one member of Congress as the public-domain congress-legislators dataset
(the @unitedstates project) describes them: their terms, party and state. Where the records
come from is dataset.py.
"""

from datetime import date

from ..names import squash

SINCE = date(2011, 1, 1)      # eFD starts in 2012; keep people who served from 2011 on
PARTY_LETTERS = {"Democrat": "D", "Republican": "R", "Independent": "I"}


class Legislator:
    def __init__(self, record, today):
        self.today = today                      # whether someone still serves depends on the day
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
