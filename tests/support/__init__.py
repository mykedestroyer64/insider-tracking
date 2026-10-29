"""
What the test files share:

  checks.py      PASS/FAIL lines and the exit code
  database.py    the test database (a fresh one per test file) and reading it back
  psql_driver.py a stand-in database driver for computers without pg8000
  commands.py    running a command and catching what it prints
  platforms.py   the outside world the code under test gets (test database, settings, clock)
  web.py         starting a stand-in site
"""
