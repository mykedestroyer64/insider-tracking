"""
steps.py - the numbered step lines of a run:

    [3/5] Check for new reports................ OK - 12 reports received since 08/30/2026, 2 new

Each finished step prints one line and is kept for the run's record in the database.
"""

from dataclasses import dataclass, field


@dataclass
class StepResult:
    """What a finished step reports: a short explanation and the HTTP codes it saw, if any."""
    detail: str = ""
    codes: list = field(default_factory=list)


class StepLog:
    """names: every step's name, step 1 first. label_width: how far the dots go.
    http_codes: whether the lines (and the record) show the HTTP codes a step saw."""

    def __init__(self, names, console, label_width=38, http_codes=True):
        self.names = list(names)
        self.console = console
        self.label_width = label_width
        self.http_codes = http_codes
        self.entries = []

    def done(self, number, status, detail="", codes=()):
        name = self.names[number - 1]
        label = f"{name} ".ljust(self.label_width, ".")
        entry = {"step": number, "name": name, "status": status}
        http = ""
        if self.http_codes:
            codes = list(codes)
            http = f" (HTTP {' -> '.join(str(code) for code in codes)})" if codes else ""
            if detail.startswith("HTTP "):
                http = ""   # the explanation already starts with the HTTP code
            entry["http_codes"] = codes
        entry["detail"] = detail
        self.console.line(f"[{number}/{len(self.names)}] {label} {status}{http}" + (f" - {detail}" if detail else ""))
        self.entries.append(entry)

    def rest(self, after, status):
        """Give every step after `after` the same status (NOT_RUN or SKIPPED)."""
        for number in range(after + 1, len(self.names) + 1):
            self.done(number, status)
