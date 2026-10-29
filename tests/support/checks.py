"""checks.py - one PASS or FAIL line per check, and the exit code at the end."""


class Checks:
    def __init__(self):
        self.passed = 0
        self.failed = []

    def __call__(self, name, condition, detail=""):
        """One check: prints PASS or FAIL (and `detail` when it failed); returns whether it passed."""
        ok = bool(condition)
        if ok:
            self.passed += 1
        else:
            self.failed.append(name)
        print(("  PASS  " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
        return ok

    @staticmethod
    def section(title):
        print(f"\n== {title}")

    def finish(self):
        """Prints the last line; the exit code (0 when every check passed)."""
        if self.failed:
            print(f"\n{len(self.failed)} of {self.passed + len(self.failed)} checks FAILED")
            return 1
        print(f"\nALL {self.passed} CHECKS PASSED")
        return 0
