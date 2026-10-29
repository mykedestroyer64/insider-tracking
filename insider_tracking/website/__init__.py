"""
The website: a static site built from the database and published on GitHub Pages.

  config.py       where the site lives (base path, address) and where its files are
  loading.py      reading the database as text (one way for every loader)
  names.py        names and page addresses (slugs)
  formatting.py   numbers, money, dates and percentages as the pages show them
  congress/       members of Congress: models, loading, grouping and statistics
  insiders/       company insiders (US and Europe): models, the rules for what counts, loading
  people/         party and state from the public legislators list (where it comes from,
                  each member, the corrections file) and the name matching
  returns/        how each trade's stock did afterwards
  charts.py       the SVG charts
  render.py       the templates (site/templates) and what they can use
  writer.py       writing the output folder (pages, data files, redirects, the sitemap's list)
  pages/          one builder per group of pages
  build.py        the whole build, start to finish (`python -m insider_tracking site build`)

The templates, the stylesheet, the script and the fonts are in the site/ folder.
"""
