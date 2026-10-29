"""
framework.py - how an access check goes, the same for every site.

Each step's line is printed as the step begins and finished when it ends, so a crash still
shows which step it was in:

    [2/5] Accept the usage agreement......... OK (HTTP 302 -> 200) - now on /search/

Each step gets one status:
  OK         it worked
  BLOCKED    the site refused us (HTTP 403/429 or an "Access Denied" page)
  SITE_DOWN  the site had a server error or showed a maintenance page
  CHANGED    a page did not look the way the check expects
Steps that never ran because an earlier step failed are marked NOT_RUN.

A check is polite on purpose: it waits 2-3 seconds between requests, gives every request a
time limit, retries at most once, and sends a User-Agent that names the project and gives a
contact email. If the site blocks it, it says so and stops. It never tries to get around a
block. The last line printed is the verdict. Exit code: 0 if OK, 1 otherwise.
"""

import argparse
import json
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import PROJECT
from ..core.errors import SiteStop
from ..core.identity import user_agent
from ..core.status import CHANGED, NOT_RUN, OK
from ..runs.console import Console, tolerate_unusual_characters
from ..runs.platform import REPOSITORY
from ..runs.settings import Settings
from ..web.client import PoliteClient, new_session
from ..web.describe import http_codes, short_error
from ..web.pacing import Pacing

PACING = Pacing(pause=(2.0, 3.0), retry_extra=5, timeout=(10, 30))
VERSION = "0.1"


@dataclass(frozen=True)
class CheckDefinition:
    title: str                 # the first line says "<title> - insider-tracking 0.1"
    description: str           # what --help says the check does
    steps: tuple
    site: str                  # "nothing was sent to <site>" when no email is given
    folder: str = ""           # results go to output/<folder>
    label_width: int = 40
    http_rule: bool = False    # leave out "(HTTP ...)" when the explanation starts with "HTTP "
    http_status: bool = False  # each step's record also keeps its last HTTP status
    problem_page_limit: int | None = 500_000
    pacing: Pacing = PACING
    advice: dict = field(default_factory=dict)   # status (or (status, ran_on)) -> lines


class CheckStepLog:
    """Prints each step's result and keeps it for summary.json."""

    def __init__(self, definition, console):
        self.definition = definition
        self.console = console
        self.steps = []
        self.current = 0

    def begin(self, number):
        self.current = number
        name = self.definition.steps[number - 1]
        label = f"{name} ".ljust(self.definition.label_width, ".")
        self.console.start(f"[{number}/{len(self.definition.steps)}] {label}")

    def end(self, status, detail="", codes=()):
        codes = list(codes)
        http = f" (HTTP {' -> '.join(str(code) for code in codes)})" if codes else ""
        if self.definition.http_rule and detail.startswith("HTTP "):
            http = ""   # the explanation already starts with the HTTP code
        self.console.line(f"{status}{http}" + (f" - {detail}" if detail else ""))
        entry = {"step": self.current, "name": self.definition.steps[self.current - 1], "status": status}
        if self.definition.http_status:
            entry["http_status"] = codes[-1] if codes else None
        entry.update({"http_codes": codes, "detail": detail})
        self.steps.append(entry)

    def mark_rest(self, status, detail=""):
        """Give every step after the current one the same status (NOT_RUN or SKIPPED)."""
        for number in range(self.current + 1, len(self.definition.steps) + 1):
            self.begin(number)
            self.end(status, detail)


@dataclass
class CheckRun:
    """What one run of a check knows; the check reads and fills it."""
    console: Console
    log: CheckStepLog
    folder: object             # pathlib.Path of the results
    ran_on: str
    today: object
    started: datetime
    status: str = OK
    outcome: str = ""
    advice: list | None = None
    failure: SiteStop | None = None
    failed_step: int | None = None
    info: dict = field(default_factory=dict)


class Check:
    """One site's check. The runner calls these, in this order."""

    definition: CheckDefinition

    def clear(self, folder):
        """Remove the results of an earlier run."""

    def intro(self, run):
        """Lines printed after "Running on" (none by default)."""
        return []

    def no_email(self, run):
        """Lines printed when there is no contact email (nothing is sent anywhere)."""
        return [f"\nNo valid contact email was given, so nothing was sent to {self.definition.site}."]

    def run_steps(self, run, client):
        """The steps (log.begin / log.end); SiteStop stops them. Fills run.info."""
        raise NotImplementedError

    def outcome(self, run):
        """The end of the verdict line when every step worked (may also set run.advice)."""
        raise NotImplementedError

    def summary(self, run, verdict_line):
        """summary.json's content."""
        raise NotImplementedError

    def saved_line(self, run):
        """'Saved in ...': what the run left in the output folder."""
        raise NotImplementedError

    def finish(self, run):
        """Last words before the summary is written (e.g. paths made relative)."""


class CheckRunner:
    def __init__(self, check):
        self.check = check

    def parser(self):
        parser = argparse.ArgumentParser(description=self.check.definition.description)
        parser.add_argument("--email", help="your contact email, sent in the User-Agent")
        return parser

    def run(self, argv, platform):
        check, definition = self.check, self.check.definition
        args = self.parser().parse_args(argv)
        tolerate_unusual_characters()
        console = Console()
        folder = platform.output_dir / definition.folder if definition.folder else platform.output_dir
        folder.mkdir(parents=True, exist_ok=True)
        check.clear(folder)
        run = CheckRun(console=console, log=CheckStepLog(definition, console), folder=folder,
                       ran_on=platform.ran_on, today=platform.clock.today(), started=platform.clock.now())

        console.line(f"{definition.title} - {PROJECT} {VERSION}")
        console.line(f"Running on: {run.ran_on}")
        console.lines(check.intro(run))

        settings = Settings.read(platform.environ, args.email)
        if not settings.has_valid_email:
            console.lines(check.no_email(run))
            run.status, run.outcome = NOT_RUN, "- no contact email given"
        else:
            console.line("Contact email: set (it goes in the User-Agent and is not saved in output)\n")
            client = PoliteClient(new_session(user_agent(VERSION, settings.email)), definition.pacing,
                                  sleep=platform.sleep)
            self._steps(run, client)
            if run.failure is not None:
                run.status = run.failure.status
                name = definition.steps[run.failed_step - 1]
                run.outcome = f"at step {run.failed_step} ({name}) - {run.failure.detail}"
            else:
                run.status = OK
                run.outcome = check.outcome(run)

        check.finish(run)
        verdict_line = f"VERDICT: {run.status} {run.outcome} (ran on {run.ran_on})"
        write_json(folder / "summary.json", check.summary(run, verdict_line))
        console.line()
        advice = run.advice or definition.advice.get((run.status, run.ran_on)) or definition.advice.get(run.status)
        if advice:
            console.lines(advice)
        console.line(check.saved_line(run))
        console.line(verdict_line)
        return 0 if run.status == OK else 1

    def _steps(self, run, client):
        definition = self.check.definition
        try:
            self.check.run_steps(run, client)
        except SiteStop as error:
            run.failure, run.failed_step = error, run.log.current
            run.log.end(error.status, error.detail, http_codes(error.response))
            run.log.mark_rest(NOT_RUN)
            if error.response is not None:
                limit = definition.problem_page_limit
                content = error.response.content
                (run.folder / "problem_page.html").write_bytes(content if limit is None else content[:limit])
        except Exception as error:   # the check tripped over something it did not expect
            run.failure = SiteStop(CHANGED, f"unexpected problem: {short_error(error)}")
            run.failed_step = run.log.current
            run.log.end(CHANGED, run.failure.detail)
            run.log.mark_rest(NOT_RUN)
            run.console.line("\nTechnical details (useful for fixing the script):")
            traceback.print_exc(file=sys.stdout)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def common_summary(run, verdict_line):
    """The fields every check's summary.json starts with."""
    return {"verdict": run.status, "verdict_line": verdict_line, "ran_on": run.ran_on}


def times(run):
    return {"started_at_utc": run.started.isoformat(timespec="seconds"),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def script_fields():
    return {"script": f"{PROJECT} {VERSION}", "python": sys.version.split()[0]}


def shown(path):
    """A path relative to the repository when possible (shorter to read)."""
    try:
        return str(path.relative_to(REPOSITORY))
    except ValueError:
        return str(path)
