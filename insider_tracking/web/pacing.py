"""
pacing.py - how gently a client talks to a site, and how many requests a run may send.

Pacing          the waits: between requests, before the one retry, after "slow down" (429)
RequestBudget   the most requests one run may send; the run ends quietly when they are used up
"""

from dataclasses import dataclass, replace

from ..core.errors import OutOfRequests


@dataclass(frozen=True)
class Pacing:
    pause: tuple            # seconds to wait between requests (picked at random in this range)
    retry_extra: float      # extra seconds to wait before the one retry
    timeout: tuple          # seconds allowed to connect, and to receive the answer
    slow_down_wait: float | None = None   # after "429 Too Many Requests": wait, then retry once
                                          # (None: a 429 is answered like any refusal)

    def at_least(self, seconds):
        """The same pacing, but never less than `seconds` between requests (robots.txt's crawl delay)."""
        if seconds and seconds > self.pause[0]:
            return replace(self, pause=(float(seconds), float(seconds) + 1.0))
        return self

    def without_waiting(self):
        """The same pacing with no waits at all (for tests against a local stand-in site)."""
        return replace(self, pause=(0, 0), retry_extra=0)


class RequestBudget:
    """Counts the requests sent; `limit` None means no limit."""

    def __init__(self, limit=None):
        self.limit = limit
        self.sent = 0

    @property
    def left(self):
        return max(0, self.limit - self.sent) if self.limit is not None else None

    def check(self):
        """Raise OutOfRequests when no request is left."""
        if self.limit is not None and self.sent >= self.limit:
            raise OutOfRequests()

    def count(self):
        self.sent += 1
