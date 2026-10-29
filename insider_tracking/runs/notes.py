"""notes.py - lines GitHub shows as notes on a run (readable through its API without the logs)."""


class Notes:
    """note(text) prints one note titled `title` on `console`; with `limit`, longer notes are cut there."""

    def __init__(self, title, console, limit=None):
        self.title = title
        self.console = console
        self.limit = limit

    def __call__(self, text):
        text = text.replace("\n", " ")
        self.console.line(f"::notice title={self.title}::" + (text if self.limit is None else text[:self.limit]))
