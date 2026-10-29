"""
names.py - how names in Finansinspektionen's register are tidied, for the collector and the
website: people and companies written in capitals, company-form suffixes ("AB (publ)"), and
"people" who are really companies, funds or foundations (the site keeps people only).
"""

import re

# Words that mean a "person" is really a company, fund or foundation (Swedish and English forms)
ENTITY_WORDS = (r"\b(?:AB|Aktiebolag|AS|ASA|A/S|ApS|Oy|Oyj|KB|HB|Ltd|Limited|LLC|L\.?P\.?|Inc|Corp|GmbH|AG|SE|SA|S\.A\.|"
                r"PLC|Holding|Holdings|Invest|Investment|Investments|Förvaltning|Förvaltnings|Kapital|Capital|Stiftelse|"
                r"Stiftelsen|Foundation|Fund|Fonder|Partners|Trust|Group|Gruppen|Bolag|Bolaget|Fastigheter|Konsult)\b")
ENTITY = re.compile(ENTITY_WORDS, re.I)

COMPANY_SUFFIXES = re.compile(r"\s*,?\s*(?:\(publ\)|AB|Aktiebolag|ASA|AS|Oyj|Oy|Abp|A/S|ApS|plc|SE|Ltd\.?|Limited|NV|"
                              r"N\.V\.|SA|S\.A\.|AG|Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|LLC|L\.?P\.?)\s*$", re.I)


def tidy(text):
    """Collapse the register's non-breaking and repeated spaces."""
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def is_entity(name):
    """True for names that are companies, foundations or funds rather than people."""
    name = tidy(name)
    return bool(ENTITY.search(name)) or name[:1].isdigit()


def person_display_name(name):
    """Register names are mostly normal; fix the ones written in capitals ('ANNA SVENSSON')."""
    name = tidy(name)
    if name and name == name.upper() and any(c.isalpha() for c in name):
        return " ".join("-".join(part.capitalize() for part in word.split("-")) for word in name.split())
    return name


def company_display_name(name):
    """'EASTNINE AB (PUBL)' -> 'Eastnine AB (publ)'; names already in normal case are kept."""
    name = tidy(name)
    if name == name.upper() and any(c.isalpha() for c in name):
        words = []
        for word in name.split():
            low = word.lower()
            if low in ("ab", "asa", "as", "plc", "se", "ag", "sa", "nv", "oyj", "oy", "ltd", "kb", "hb", "ab,"):
                words.append(word.upper() if low not in ("oyj", "oy", "ltd") else word.capitalize())
            elif low in ("(publ)", "(publ)."):
                words.append("(publ)")
            elif len(word) <= 3 and word.isalpha():
                words.append(word)            # ICA, SEB, SKF, AAK, NCC, ...
            else:
                words.append("-".join(p.capitalize() for p in word.split("-")))
        name = " ".join(words)
    return name


def company_short_name(name):
    """The name without company-form suffixes: 'Corem Property Group AB' -> 'Corem Property Group'."""
    name = company_display_name(name)
    short = name
    for _ in range(3):
        trimmed = COMPANY_SUFFIXES.sub("", short)
        if trimmed == short or not trimmed:
            break
        short = trimmed
    return short.strip(" ,.") or name
