"""
errors.py - the two ways a website conversation ends early.

SiteStop      the site refused us, is down, or works differently: the run stops with a status
OutOfRequests the run has used every request it is allowed: not an error, the run just ends
"""


class SiteStop(Exception):
    """A site refused us, is down, or answered something unexpected: the run must stop.

    status     one of core.status (BLOCKED, SITE_DOWN, CHANGED, KEY_PROBLEM)
    detail     a short explanation for the verdict line
    response   the answer that caused it (kept so the page can be saved), if any
    reference  Akamai's "Reference #..." text, when the refusal came from Akamai
    """

    def __init__(self, status, detail, response=None, reference=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.response = response
        self.reference = reference

    def extended(self, more):
        """The same stop with more words at the end of its explanation."""
        return SiteStop(self.status, f"{self.detail}{more}", self.response, self.reference)


class OutOfRequests(Exception):
    """This run has used all the requests it is allowed (not an error)."""
