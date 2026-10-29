"""
robots.py - a site's rules for scripts (its robots.txt): whether the addresses a script wants
may be read, and how long the site asks it to wait between requests.
"""

from dataclasses import dataclass
from urllib import robotparser


@dataclass(frozen=True)
class RobotRules:
    allows_all: bool              # every address asked about may be read
    delay: float | None = None    # the wait between requests the site asks for, if any


def read_rules(text, addresses, agent="*"):
    """The rules in a robots.txt for these addresses."""
    parser = robotparser.RobotFileParser()
    parser.parse(text.splitlines())
    return RobotRules(all(parser.can_fetch(agent, address) for address in addresses), parser.crawl_delay(agent))
