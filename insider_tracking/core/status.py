"""
status.py - the statuses a run, a step or a check ends with.

They appear at the start of every verdict line ("VERDICT: OK - ...") and in the database
(collector_runs.verdict), so they are plain words that never change.
"""

OK = "OK"                    # it worked
BLOCKED = "BLOCKED"          # the site refused the request (the run stops; no workarounds)
SITE_DOWN = "SITE_DOWN"      # the site had a server error or showed a maintenance page
CHANGED = "CHANGED"          # a page or file did not look the way the code expects
KEY_PROBLEM = "KEY_PROBLEM"  # an API key is missing, wrong, or doesn't cover what was asked
DB_ERROR = "DB_ERROR"        # the database could not be reached, read or written
ERROR = "ERROR"              # a problem nobody expected (a bug)
NOT_RUN = "NOT_RUN"          # never ran: an earlier step failed, or a setting is missing
SKIPPED = "SKIPPED"          # not needed (or no requests left) in this run
