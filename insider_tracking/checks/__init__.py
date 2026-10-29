"""
The access checks: can a script read this site, and what does it send? Each check is a short
list of steps against one site, run by hand (the "eFD access check" and "New sources check"
workflows), saving real examples so a collector can be built or fixed from them.

framework.py runs any check (settings, numbered steps, verdict, summary.json); each other
module is one check and only knows its own site.
"""
