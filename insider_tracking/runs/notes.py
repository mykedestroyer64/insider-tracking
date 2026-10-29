"""notes.py - lines GitHub shows as notes on a run (readable through its API without the logs)."""


class Notes:
    """note(text) prints one note titled `title`; with `limit`, longer notes are cut there."""

    def __init__(self, title, limit=None):
        self.title = title
        self.limit = limit

    def __call__(self, text):
        text = text.replace("\n", " ")
        print(f"::notice title={self.title}::" + (text if self.limit is None else text[:self.limit]))
