"""
platform.py - the outside world a command runs in: environment variables, the database
driver, the clock, the output folder, and how to wait. Commands get it from outside, so a
test can hand them a stand-in for any part (a local database, a fixed day, no waiting).
"""

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from ..core.clock import Clock, SystemClock
from ..db.connection import connect
from .settings import ran_on

REPOSITORY = Path(__file__).resolve().parents[2]      # the folder with README.md
OUTPUT = REPOSITORY / "output"


@dataclass
class Platform:
    environ: Mapping = field(default_factory=lambda: os.environ)
    connect: Callable = connect          # (url, password) -> a DB-API connection
    clock: Clock = field(default_factory=SystemClock)
    output_dir: Path = OUTPUT
    sleep: Callable = time.sleep

    @property
    def ran_on(self):
        return ran_on(self.environ)
