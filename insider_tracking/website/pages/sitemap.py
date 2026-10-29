"""sitemap.py - the sitemap (every page written, in order) and robots.txt, for search engines."""

from html import escape


class Sitemap:
    def build(self, site):
        today = site.today.isoformat()
        urls = "".join(f"<url><loc>{escape(site.config.url + p)}</loc><lastmod>{today}</lastmod></url>"
                       for p in site.writer.pages)
        site.writer.write("/sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>'
                          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + "</urlset>")
        site.writer.write("/robots.txt", f"User-agent: *\nAllow: /\nSitemap: {site.config.url}/sitemap.xml\n")
