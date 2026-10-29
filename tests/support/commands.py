"""commands.py - running a command the way the command line does, and catching what it prints."""

import contextlib
import io
from dataclasses import dataclass


@dataclass
class Result:
    code: int        # the exit code
    out: str         # everything printed

    @property
    def lines(self):
        return self.out.strip().splitlines()

    @property
    def last(self):
        """The last line printed (a run's verdict)."""
        return self.lines[-1] if self.lines else ""


def run(command, argv, platform):
    """Run a command (anything with .run(argv, platform)); what it printed comes back in the Result."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = command.run(list(argv), platform)
    return Result(code, buffer.getvalue())
