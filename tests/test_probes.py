"""
The two smallest probes, which only post notes: which daily lists EDGAR has (check secdays,
against a stand-in), and which library versions are installed (check libs).
"""
import json
import sys
from types import SimpleNamespace

from insider_tracking.checks.libs import LibsCheck
from insider_tracking.checks.secdays import SecDaysCheck
from insider_tracking.runs.platform import Platform

from .support.checks import Checks
from .support.commands import run as run_command
from .support.web import StandInHandler, serve

REQUESTS = []
LISTING = {"directory": {"item": [{"name": "form.20261009.idx"}, {"name": "form.20261013.idx"},
                                  {"name": "master.20261013.idx"}], "name": "QTR4"}}


class Handler(StandInHandler):
    def do_GET(self):
        REQUESTS.append((self.path, self.headers.get("User-Agent")))
        if self.path.endswith("/index.json"):
            return self.reply(200, json.dumps(LISTING), "application/json")
        if self.path.endswith("form.20261013.idx"):
            return self.reply(200, "Description: Daily Index of EDGAR\nForm Type   Company Name\n", "text/plain")
        return self.reply(403, '<?xml version="1.0"?><Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>',
                          "application/xml")


BASE = serve(Handler)
check = Checks()

check.section("secdays")
probe = SecDaysCheck(quarter=BASE + "/Archives/edgar/daily-index/2026/QTR4/")
result = run_command(probe, [], Platform(environ={"EFD_CONTACT_EMAIL": "tester@example.com"}, sleep=lambda seconds: None))
print(result.out)
check("3 requests: the listing and the two days", [path.rsplit("/", 1)[-1] for path, _ in REQUESTS]
      == ["index.json", "form.20261012.idx", "form.20261013.idx"], REQUESTS)
check("the listing's days are named", "2 form.*.idx files: 20261009, 20261013" in result.out, result.out[:300])
check("the missing day answers 403 Access Denied", "form.20261012.idx: HTTP 403" in result.out and "AccessDenied" in result.out)
check("the existing day answers 200", "form.20261013.idx: HTTP 200" in result.out)
check("the User-Agent names the project and the contact email",
      all(agent.startswith("insider-tracking/") and "tester@example.com" in agent for _, agent in REQUESTS), REQUESTS[:1])
check("exit code 0, notes only", result.code == 0 and all(line.startswith("::notice title=SECDAYS check::")
                                                          for line in result.lines))

check.section("libs")
versions = {"pg8000": "1.31.5", "scramp": "1.4.17"}


def version(name):
    if name not in versions:
        from importlib.metadata import PackageNotFoundError
        raise PackageNotFoundError(name)
    return versions[name]


fake_scramp = SimpleNamespace(make_channel_binding=None, ScramClient=None)
result = run_command(LibsCheck(names=("pg8000", "scramp", "jinja2"), version=version, load=lambda name: fake_scramp),
                     [], Platform(environ={}))
print(result.out)
check("installed versions, and a missing one", "installed: pg8000 1.31.5, scramp 1.4.17, jinja2 missing" in result.out,
      result.out)
check("scramp still has make_channel_binding", "scramp has make_channel_binding: True" in result.out, result.out)


def no_scramp(name):
    raise ImportError(name)


result = run_command(LibsCheck(names=("scramp",), version=version, load=no_scramp), [], Platform(environ={}))
check("without scramp it says so", "scramp is not installed" in result.out and result.code == 0, result.out)
sys.exit(check.finish())
