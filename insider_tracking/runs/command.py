"""
command.py - `python -m insider_tracking collect <name>`: one collector run, start to finish.

It reads the command-line options, prints the run's first lines, checks the settings
(nothing is sent anywhere when one is missing) and hands over to the runner.
"""

import argparse

from .. import PROJECT
from ..core.status import NOT_RUN
from .archive import NoArchive, ProblemPageArchive
from .console import Console, tolerate_unusual_characters
from .runner import CollectorRunner
from .settings import Settings
from .source import RunContext
from .storage import RunStorage


class CollectorCommand:
    def __init__(self, source):
        self.source = source

    def parser(self):
        definition = self.source.definition
        parser = argparse.ArgumentParser(description=definition.description)
        if definition.takes_email:
            parser.add_argument("--email", help="your contact email, sent in the User-Agent")
        parser.add_argument("--max-requests", type=int, default=definition.max_requests,
                            help=definition.max_requests_help())
        return parser

    def run(self, argv, platform):
        definition = self.source.definition
        args = self.parser().parse_args(argv)
        tolerate_unusual_characters()
        ran_on = platform.ran_on
        started, today = platform.clock.now(), platform.clock.today()
        archive_type = ProblemPageArchive if definition.keeps_problem_page else NoArchive
        archive = archive_type(platform.output_dir)
        archive.clear()

        console = Console()
        console.line(f"{PROJECT} {definition.title} {definition.version}")
        console.line(f"Running on: {ran_on}")
        console.line(definition.budget_line(args.max_requests) + "\n")

        settings = Settings.read(platform.environ, getattr(args, "email", None))
        for precondition in definition.preconditions:
            refusal = precondition.refusal(settings)
            if refusal is not None:
                console.lines(refusal.lines)
                console.line(f"VERDICT: {NOT_RUN} {refusal.outcome} (ran on {ran_on})")
                return refusal.exit_code

        context = RunContext(db=None, settings=settings, max_requests=args.max_requests, started=started,
                             today=today, console=console, sleep=platform.sleep)
        runner = CollectorRunner(definition, console, RunStorage(platform.connect), archive, ran_on)
        return runner.run(self.source, context)
