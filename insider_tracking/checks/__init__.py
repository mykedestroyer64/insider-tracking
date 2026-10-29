"""
The access checks: can a script read this site, and what does it send? Each check is a short
list of steps against one site, run by hand (the "eFD access check" and "New sources check"
workflows), saving real examples so a collector can be built or fixed from them.

framework.py runs any check (settings, numbered steps, verdict, summary.json); efd.py,
house.py, sec.py, sweden.py and netherlands.py are one check each and only know their own site.

sharadar.py, secdays.py and libs.py are smaller probes: they only post notes on the run (what
an API's answers look like, which daily lists EDGAR has, which library versions are installed).
"""
