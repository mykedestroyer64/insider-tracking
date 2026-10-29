"""archive.py - keeps the page that stopped a run (output/problem_page.html), to see what the site sent."""

PROBLEM_PAGE = "problem_page.html"
LIMIT = 2_000_000          # bytes kept of a very long page


class ProblemPageArchive:
    def __init__(self, folder, name=PROBLEM_PAGE):
        self.folder = folder
        self.name = name

    def clear(self):
        """Remove the page an earlier run kept."""
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / self.name).unlink(missing_ok=True)

    def keep(self, response):
        (self.folder / self.name).write_bytes(response.content[:LIMIT])


class NoArchive:
    """For runs whose problem pages aren't worth keeping (an API that answers in one line)."""

    def __init__(self, folder):
        self.folder = folder

    def clear(self):
        self.folder.mkdir(parents=True, exist_ok=True)

    def keep(self, response):
        pass
