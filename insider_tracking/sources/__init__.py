"""
One folder per data source. Each has the same three kinds of module:

  site.py       the website gateway: the source's addresses and how to ask for each page
                (through web.client.PoliteClient; no parsing, no database)
  parsing.py    turns what the site sends into clean records (no network, no database)
  collector.py  the collector's definition, how its run is put together, and its steps,
                using the two above and db/ (a long step gets its own module: the SEC's
                quarterly.py and daily.py)

Shared by the sources:
  ports.py         the small interfaces the collectors' steps are written against
  report_queue.py  reading a queue of reports one by one (Senate and House)
  congress.py      the rules the Senate's and the House's reports share
"""
