"""
insider_tracking - collects the stock trades that members of Congress and company insiders
must disclose, and builds the public website from them.

Run a part of it with   python -m insider_tracking <group> <name>   (see cli.py), e.g.
    python -m insider_tracking collect sec
    python -m insider_tracking site build --out _site

How the code is organised (each folder has one job, and depends only on the ones above it):

  core/      words every part shares: statuses, the errors that stop a run, the clock
  web/       the polite web client (waits between requests, retries once, keeps to a budget)
             and the checks that tell a refusal or an outage from a normal answer
  db/        the database: connecting, the tables, and one "repository" per group of tables
  runs/      how a collector run goes: settings check, numbered steps, verdict, run record
  sources/   one folder per data source (Senate, House, SEC, Sweden, ECB, Sharadar, returns):
             its website gateway, its parsing rules and its collector steps
  checks/    the access checks (can a script read this site?) built on the same pieces
  tools/     database maintenance (size report, freeing space)
  website/   the static website: loading the data, the rules for what counts, the pages

A new data source is a new folder in sources/ plus one line in cli.py: nothing else changes.
"""

PROJECT = "insider-tracking"
