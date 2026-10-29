"""identity.py - how the project introduces itself to the sites it reads."""

from .. import PROJECT


def user_agent(version, email=None):
    """'insider-tracking/0.5 (non-commercial public transparency project; contact: you@example.com)'.

    Sites that allow scripts ask them to say who they are and how to reach their owner.
    """
    contact = f"; contact: {email}" if email else ""
    return f"{PROJECT}/{version} (non-commercial public transparency project{contact})"
