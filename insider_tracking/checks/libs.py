"""
libs.py - which versions of the Python libraries GitHub installed (from requirements.txt), and
whether scramp still offers what pg8000 needs to log in (on 25 Oct 2026 scramp 1.5.0 removed
make_channel_binding and every collector failed at "Connect to the database").
Notes only; the newest versions on PyPI are shown by the workflow (pip index versions).
"""

import importlib
import importlib.metadata as metadata

from ..runs.notes import Notes

NAMES = ("pg8000", "scramp", "asn1crypto", "requests", "beautifulsoup4", "jinja2", "pdfplumber")


def installed(name, version=metadata.version):
    try:
        return f"{name} {version(name)}"
    except metadata.PackageNotFoundError:
        return f"{name} missing"


class LibsCheck:
    """`check libs`. load(name) imports a module (tests pass a stand-in)."""

    def __init__(self, names=NAMES, version=metadata.version, load=importlib.import_module,
                 note=Notes("LIBS check")):
        self.names = names
        self.version = version
        self.load = load
        self.note = note

    def run(self, argv, platform):
        self.note("installed: " + ", ".join(installed(name, self.version) for name in self.names))
        try:
            scramp = self.load("scramp")
        except ImportError:
            self.note("scramp is not installed")
            return 0
        self.note("scramp has make_channel_binding: " + str(hasattr(scramp, "make_channel_binding"))
                  + "; scramp offers: " + ", ".join(sorted(n for n in dir(scramp) if not n.startswith("_"))))
        return 0


def command():
    """`python -m insider_tracking check libs`."""
    return LibsCheck()
