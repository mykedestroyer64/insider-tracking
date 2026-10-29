"""console.py - where a run's lines go: the terminal (or whatever stands in for it in a test)."""

import sys


class Console:
    """Prints lines to sys.stdout as it is when each line is printed (errors to sys.stderr)."""

    def line(self, text=""):
        print(text, flush=True)

    def write(self, text):
        """Text as it is, without adding a line break (e.g. a traceback)."""
        print(text, end="", flush=True)

    def error(self, text):
        """A warning that isn't part of the run's output."""
        print(text, file=sys.stderr, flush=True)

    def start(self, text):
        """The start of a line whose end comes later (a check's step, printed as it begins)."""
        print(text, end=" ", flush=True)

    def lines(self, texts):
        for text in texts:
            self.line(text)

    def note(self, text):
        """A progress line printed while a step is still working (indented under the step lines)."""
        self.line(f"      {text}")


def tolerate_unusual_characters():
    """Never crash on a character the terminal can't show (it is replaced instead)."""
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
