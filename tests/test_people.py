"""
Matching the people who file reports to the legislators list (website/people/matcher.py) and
the hand-made corrections in site/overrides.json: a bioguide ID places a member the matcher
can't; "candidate" lists someone who ran for Congress but was never a member with the
candidates, and the build log stops asking about them. Also: the real overrides file.
"""
import json
import sys
from datetime import date
from pathlib import Path

from insider_tracking.website.congress.models import Filer
from insider_tracking.website.people.legislators import Legislator
from insider_tracking.website.people.matcher import CANDIDATE, Matcher

from .support.checks import Checks

check = Checks()
TODAY = date(2026, 10, 31)
PELOSI = Legislator({"id": {"bioguide": "P000197"}, "name": {"first": "Nancy", "last": "Pelosi"},
                     "terms": [{"type": "rep", "start": "1987-06-02", "end": "2027-01-03", "party": "Democrat",
                                "state": "CA", "district": 11}]}, TODAY)


def house_filer(last, first, state):
    return Filer(key=f"house|{last}|{first}|{state}", first_name=first, last_name=last, name=f"{first} {last}",
                 slug=f"{first}-{last}".lower(), filer_type="Representative", chamber="house", district=f"{state}1")


check.section("the corrections file")
notes = []
matcher = Matcher([PELOSI], {"house|Speaker|N.|CA": "P000197", "house|Reisdorf|Richard B.|MN": CANDIDATE},
                  log=notes.append)
placed, candidate, unknown = (house_filer("Speaker", "N.", "CA"), house_filer("Reisdorf", "Richard B.", "MN"),
                              house_filer("Nobody", "Some", "TX"))
for filer in (placed, candidate, unknown):
    matcher.apply(filer)
check("a bioguide ID places a member the matcher can't", placed.bioguide == "P000197" and placed.party is not None,
      (placed.bioguide, placed.party))
check("\"candidate\": listed as a candidate, not as a member", candidate.filer_type == "Candidate"
      and not candidate.is_member, candidate.filer_type)
check("and the build log doesn't ask about them", candidate not in matcher.unmatched)
check("a representative nobody placed is still listed in the build log, apart from the members",
      matcher.unmatched == [unknown] and unknown.filer_type == "House filer", [f.key for f in matcher.unmatched])
check("only the bioguide match counts as matched", matcher.matched == 1, matcher.matched)

check.section("site/overrides.json")
overrides = json.loads((Path(__file__).resolve().parents[1] / "site" / "overrides.json").read_text(encoding="utf-8"))
values = {key: value for key, value in overrides.items() if not key.startswith("_")}
check("every value is a bioguide ID or \"candidate\"",
      all(value == CANDIDATE or (isinstance(value, str) and len(value) == 7 and value[0].isalpha() and value[1:].isdigit())
          for value in values.values()), values)
check("every key is a filer key ('senate|Last|First' or 'house|Last|First|ST')",
      all(key.startswith(("senate|", "house|")) and key.count("|") in (2, 3) for key in values), list(values))
sys.exit(check.finish())
