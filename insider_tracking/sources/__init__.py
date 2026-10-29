"""
One folder per data source. Each has the same kinds of module:

  site.py       the website gateway: the source's addresses and how to ask for each page
                (through web.client.PoliteClient; no parsing, no database)
  parsing.py    turns what the site sends into clean records (no network, no database)
  run.py        the collector's steps, written against the interfaces in ports.py only (no
                database or web code): what to ask for, what to keep, the summary lines; a
                long step gets its own module (the SEC's quarterly.py and daily.py)
  collector.py  the collector's definition (name, steps, wording) and its wiring: the real
                database tables, the site gateway on the polite web client and the request
                budget, handed to the steps

Shared by the sources:
  ports.py         the small interfaces the steps are written against (tests/test_ports.py
                   checks that every real repository and gateway fits its interface)
  report_queue.py  reading a queue of reports one by one (Senate and House)
  congress.py      the rules the Senate's and the House's reports share
"""
