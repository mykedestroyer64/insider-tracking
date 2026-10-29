"""
How a collector run goes, the same for every data source:

  1. the settings are checked (preconditions.py); if one is missing nothing is sent anywhere
  2. step 1 connects to the database and brings the tables up to date
  3. the source's own steps run, each printing one line (steps.py)
  4. a refusal, an outage or a changed site stops the run with that status (runner.py)
  5. the end of the run prints the totals, the requests sent and the verdict line, and
     records the run in the database

A data source plugs in by providing a CollectorDefinition (definition.py: its name, steps
and wording) and a Source that starts a SourceRun (source.py: its steps and summary).
"""
