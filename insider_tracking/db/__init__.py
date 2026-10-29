"""
The Supabase database (PostgreSQL): connecting, the tables, and the repositories.

Each repository looks after one group of tables and offers only the operations the code
needs, so a collector depends on exactly the tables it uses:

  state.py     collector_state   the collectors' bookmarks between runs
  runs.py      collector_runs    one row per collector run, with its verdict
  congress.py  reports, transactions, report_pages   Senate and House trade reports
  insiders.py  insider_filings, insider_trades       SEC Form 4 filings
  europe.py    mar_trades        European executives' trades (Sweden first)
  fx.py        fx_rates          the ECB's euro exchange rates
  returns.py   trade_returns     how each traded stock did afterwards
  size.py      the database's size (Supabase's free plan allows 500 MB)
"""
