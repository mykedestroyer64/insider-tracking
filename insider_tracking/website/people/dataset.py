"""
dataset.py - where the legislators' records come from.

  PublicDataset   the @unitedstates project's two files (current and historical members),
                  downloaded once a week into a cache folder. If they can't be downloaded and
                  there is no copy, the site is still built, just without party and state.
  FixedRecords    a list given in advance (tests)
"""

import json
import time
import urllib.request
from pathlib import Path
from typing import Protocol

SOURCES = [
    "https://unitedstates.github.io/congress-legislators/legislators-current.json",
    "https://unitedstates.github.io/congress-legislators/legislators-historical.json",
]
CACHE_DAYS = 7


class LegislatorRecords(Protocol):
    def records(self, offline: bool, log) -> list:
        """The members' records (dicts as the dataset writes them)."""


def download(url):
    """The file's bytes (raises on any problem)."""
    request = urllib.request.Request(url, headers={"User-Agent": "insider-tracking site build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


class PublicDataset:
    """cache_dir: where last week's copies are kept; fetch(url) -> bytes; now() -> seconds since
    1970 (to tell whether a copy is fresh)."""

    def __init__(self, cache_dir, fetch=download, now=time.time):
        self.cache_dir = Path(cache_dir)
        self.fetch = fetch
        self.now = now

    def records(self, offline, log):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        records = []
        for url in SOURCES:
            path = self.cache_dir / url.rsplit("/", 1)[-1]
            if not self.fresh(path) and not offline:
                self.refresh(url, path, log)
            if path.exists():
                try:
                    records += json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    log(f"  cached {path.name} is not valid JSON, ignoring it")
            else:
                log(f"  no copy of {path.name}: party and state will be missing for some senators")
        return records

    def fresh(self, path):
        return path.exists() and (self.now() - path.stat().st_mtime) < CACHE_DAYS * 86400

    def refresh(self, url, path, log):
        """Download a new copy; the old one (if any) stays when that fails."""
        try:
            data = self.fetch(url)
            json.loads(data)                    # make sure it's valid before caching
            path.write_bytes(data)
            log(f"  downloaded {path.name} ({len(data) // 1024} KB)")
        except Exception as error:              # noqa: BLE001 - any download problem
            log(f"  could not download {url}: {error}")


class FixedRecords:
    def __init__(self, records):
        self.fixed = list(records)

    def records(self, offline, log):
        return list(self.fixed)
