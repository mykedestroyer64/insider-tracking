"""
One folder per data source. Each has the same three kinds of module:

  site.py       the website gateway: the source's addresses and how to ask for each page
                (through web.client.PoliteClient; no parsing, no database)
  parsing.py    turns what the site sends into clean records (no network, no database)
  collector.py  the collector's definition and steps, using the two above and db/

congress.py holds the rules the Senate's and the House's reports share.
"""
