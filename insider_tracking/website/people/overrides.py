"""
overrides.py - the hand-made corrections for names that don't match the legislators list
(site/overrides.json: filer key -> bioguide ID).
"""

import json
import sys
from pathlib import Path


def load_overrides(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        print(f"  overrides file {path} is not valid JSON ({error}); ignoring it", file=sys.stderr)
        return {}
