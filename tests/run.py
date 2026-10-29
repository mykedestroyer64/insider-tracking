"""
run.py - runs the test files, each in its own process, and sums up.

    python -m tests.run              every tests/test_*.py file
    python -m tests.run house sec    only test_house.py and test_sec.py

Each file prints a PASS or FAIL line per check; this prints their output as it comes, then
one line per file. The exit code is 0 only when every file passed.
"""

import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPOSITORY = TESTS.parent


def test_files(names):
    if names:
        return [TESTS / f"test_{name.removeprefix('test_').removesuffix('.py')}.py" for name in names]
    return sorted(TESTS.glob("test_*.py"))


def quoted(line):
    """The line as it is shown. A line that looks like one of GitHub's workflow commands
    ("::notice title=...::") would become a note on the test run, so it is quoted."""
    return "  | " + line if line.lstrip().startswith("::") else line


def run_file(path):
    """Run one test file; (exit code, PASS count, FAIL count, seconds)."""
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, "-u", "-m", f"tests.{path.stem}"], cwd=REPOSITORY,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    passed = failed = 0
    for line in process.stdout:
        print(quoted(line), end="", flush=True)
        passed += line.startswith("  PASS  ")
        failed += line.startswith("  FAIL  ")
    return process.wait(), passed, failed, time.monotonic() - started


def main(names):
    results = []
    for path in test_files(names):
        if not path.exists():
            print(f"no such test file: {path.name}")
            results.append((path.stem, 2, 0, 0, 0.0))
            continue
        print(f"\n######## {path.stem}", flush=True)
        results.append((path.stem, *run_file(path)))
    print("\n######## summary")
    for name, code, passed, failed, seconds in results:
        verdict = "ok" if code == 0 and failed == 0 else "FAILED"
        print(f"  {name:28} {verdict:6} {passed:4} passed {failed:3} failed  {seconds:6.1f} s")
    bad = [name for name, code, passed, failed, seconds in results if code != 0 or failed]
    print("\nEvery test file passed." if not bad else f"\nFailed: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
