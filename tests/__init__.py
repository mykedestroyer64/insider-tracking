"""
The tests. Each test_*.py file runs one part of the project against stand-ins - a local copy
of a site serving saved or made-up pages (sites/), and a real PostgreSQL test database - and
prints one PASS or FAIL line per check.

    python -m tests.run              every test file (GitHub runs this on every push)
    python -m tests.test_house       one file

See README.md in this folder for what you need on your own computer.
"""
