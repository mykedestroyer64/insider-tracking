"""
platform.py - the outside world a command runs in: environment variables, the database
driver, the clock, the output folder, how to wait, where the output goes and how a web
session is made. Commands get it from outside, so a test can hand them a stand-in for any
part (a local database, a fixed day, no waiting).
"""

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from ..core.clock import Clock, SystemClock
from ..db.connection import connect
from ..web.client import new_session
from .console import Console
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
    console: Console = field(default_factory=Console)
    new_session: Callable = new_session  # (user_agent, compressed=False) -> a requests.Session

    @property
    def ran_on(self):
        return ran_on(self.environ)
