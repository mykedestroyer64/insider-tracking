"""
cli.py - the commands:   python -m insider_tracking <group> <name> [options]

  collect senate|house|sec|sweden|returns            one collector run
  check efd|house|sec|sweden|netherlands|sharadar    one access check (can a script read the site?)
  db size|slim                                       database tools
  site build                                         build the website

Each command lives in its own module, which offers a `command()` that builds it; this list
only says where to find each one (so a new command is one new line here, and starting one
command never loads the others' code).
"""

import importlib
import sys

from .runs.platform import Platform

COMMANDS = {
    "collect senate": "insider_tracking.sources.senate.collector",
    "collect house": "insider_tracking.sources.house.collector",
    "collect sec": "insider_tracking.sources.sec.collector",
    "collect sweden": "insider_tracking.sources.sweden.collector",
    "collect returns": "insider_tracking.sources.returns.collector",
    "check efd": "insider_tracking.checks.efd",
    "check house": "insider_tracking.checks.house",
    "check sec": "insider_tracking.checks.sec",
    "check sweden": "insider_tracking.checks.sweden",
    "check netherlands": "insider_tracking.checks.netherlands",
    "check sharadar": "insider_tracking.checks.sharadar",
    "db size": "insider_tracking.tools.db_size",
    "db slim": "insider_tracking.tools.db_slim",
}


def usage():
    return "usage: python -m insider_tracking <command> [options]\ncommands:\n" + "\n".join(
        f"  {name}" for name in COMMANDS)


def main(argv=None, platform=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    name = " ".join(argv[:2])
    if name not in COMMANDS:
        print(usage(), file=sys.stderr)
        return 2
    module = importlib.import_module(COMMANDS[name])
    return module.command().run(argv[2:], platform or Platform())
