"""notes.py - lines GitHub shows as notes on a run (readable through its API without the logs)."""


class Notes:
    def __init__(self, title):
        self.title = title

    def __call__(self, text):
        print(f"::notice title={self.title}::" + text.replace("\n", " "))


def mb(size):
    return f"{(size or 0) / 1048576:.1f} MB"
