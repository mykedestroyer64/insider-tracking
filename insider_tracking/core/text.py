"""text.py - small wording helpers shared by the run summaries."""


def plural(count, word, many=None, *, separators=True):
    """'1 report', '2 reports', '1,234 securities' (many='securities').

    separators=False writes 1234 instead of 1,234 (the Senate and House collectors' style).
    """
    number = f"{count:,}" if separators else f"{count}"
    return f"{number} " + (word if count == 1 else (many or word + "s"))
