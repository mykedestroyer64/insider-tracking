"""
The database tools (python -m insider_tracking db size, db slim) on a copy of a filled test
database (tests/fixtures/site_database.sql.gz): the size report, then freeing space.
"""
import re
import sys
from pathlib import Path

from insider_tracking.tools.db_size import DatabaseSizeReport
from insider_tracking.tools.db_slim import SlimDatabase

from .support.checks import Checks
from .support.commands import run as run_command
from .support.database import TestDatabase
from .support.platforms import SUPABASE_SETTINGS, test_platform

DB = TestDatabase("it_db_tools").fresh()
DB.restore(Path(__file__).resolve().parent / "fixtures" / "site_database.sql.gz")
PLATFORM = test_platform(DB, dict(SUPABASE_SETTINGS))
check = Checks()

for name, tool, at_least in (("size", DatabaseSizeReport(), 3), ("slim", SlimDatabase(), 5), ("size", DatabaseSizeReport(), 3)):
    check.section(f"db {name}")
    result = run_command(tool, [], PLATFORM)
    print(re.sub(r"\d[\d,.]*", "N", result.out))     # sizes change from run to run: show the shape of each note
    notes = [line for line in result.lines if line.startswith("::notice title=")]
    check(f"{len(notes)} notes, every line a note", len(notes) >= at_least and len(notes) == len(result.lines), result.out[-300:])
    if name == "size":
        check("the whole database and every table are listed",
              "whole database" in result.out and "insider_trades" in result.out and "transactions" in result.out)
        check("columns of the big tables are measured", "average bytes per row by column" in result.out)
    else:
        check("before and after are shown, and what was freed", "before:" in result.out and "freed" in result.out)
        check("insider_filings was rewritten", "rewrote insider_filings" in result.out)
sys.exit(check.finish())
