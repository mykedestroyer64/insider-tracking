"""
overrides.py - the hand-made corrections for names that don't match the legislators list
(site/overrides.json: filer key -> bioguide ID).
"""

import json
from pathlib import Path


def load_overrides(path, warn):
    """The corrections ({} when there are none); warn(text) says why a broken file is ignored."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        warn(f"  overrides file {path} is not valid JSON ({error}); ignoring it")
        return {}
