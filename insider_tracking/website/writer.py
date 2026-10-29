"""
writer.py - writes the output folder: a complete static website (HTML pages, a few JSON files
for the search box and the Trades page, the stylesheet, fonts, a feed and the sitemap).
"""

import shutil
from pathlib import Path


class SiteWriter:
    def __init__(self, out, renderer, base):
        self.out = Path(out)
        self.renderer = renderer
        self.base = base
        self.pages = []           # every page's address, in the order written (for the sitemap)

    def start(self, static_folder):
        """An empty output folder with the static files (stylesheet, script, fonts)."""
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        shutil.copytree(static_folder, self.out / "static")
        (self.out / ".nojekyll").write_text("")

    def write(self, path, text):
        target = self.out / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def page(self, template, path, **context):
        """Render a template to a page; pages ending in index.html go in the sitemap."""
        html = self.renderer.render(template, page=context.pop("page", None), **context)
        self.write(path, html)
        if path.endswith("index.html"):
            self.pages.append(path[: -len("index.html")])

    def redirect(self, path, to):
        """A small page that sends visitors (and search engines) to a page's new address."""
        self.write(path, self.renderer.render("redirect.html", to=self.base + to))
