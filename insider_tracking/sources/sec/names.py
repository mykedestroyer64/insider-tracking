"""
names.py - how the SEC's names and addresses are tidied, for the collector and the website.

EDGAR writes most names in capitals and people last-name first ("COOK TIMOTHY D"); funds,
trusts and companies file Form 4s too, and the site keeps people only.
"""

import re

ENTITY = re.compile(r"\b(LLC|L\.L\.C\.?|LP|L\.P\.?|LLP|Inc\.?|Corp\.?|Co\.?|Ltd\.?|Trust|Fund|Funds|Partners|Partnership|"
                    r"Capital|Holdings|Management|Group|Advisors|Advisers|Associates|Foundation|Estate|Ventures|Investments?|"
                    r"Investors|Bank|N\.A\.?|S\.A\.?|GmbH|AG|PLC|Plc|Limited|Company|GP|\(GP\)|Opportunities|Equity|"
                    r"Master|Portfolio|Securities|Asset|Assets|International|Global|Family|Trustee|Trustees|Pension|"
                    r"Retirement|Endowment|University|College|Church|Charitable|Enterprises|Industries|Technologies|"
                    r"Systems|Services|Solutions|Sciences|Pharmaceuticals|Healthcare|Energy|Resources|Realty|Properties)\b", re.I)
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
KEEP_UPPER = {"llc", "lp", "llp", "plc", "ag", "sa", "na", "ii", "iii", "iv", "md", "phd", "cpa", "jd", "dds", "usa", "us"}


def clean_name(name):
    """'COOK TIMOTHY D' -> 'Cook Timothy D' (EDGAR writes many names in capitals)."""
    name = " ".join((name or "").split())
    if name.isupper():
        words = []
        for word in name.split():
            core = word.replace(".", "").strip(",")
            if core.lower() in KEEP_UPPER or (len(core) <= 2 and core.isalpha()):
                words.append(word)            # initials and LLC/LP stay as they are
            else:
                words.append(word.capitalize())
        name = " ".join(words)
        name = re.sub(r"\b(Mc|Mac|O')([a-z])", lambda m: m.group(1) + m.group(2).upper(), name)
    return name


def display_name(name):
    """'COOK TIMOTHY D' -> 'Timothy D Cook', 'HOLDEN RICHMOND Y JR' -> 'Richmond Y Holden Jr'.

    EDGAR writes people last-name first. Company and fund names are left in their order.
    """
    name = clean_name(name)
    if ENTITY.search(name):
        return name
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first.strip()} {last.strip()}".strip()
        words = name.split()
    else:
        words = name.split()
        if len(words) >= 2:
            suffix = []
            while len(words) > 2 and words[-1].strip(".").lower() in SUFFIXES:
                suffix.insert(0, words.pop())
            words = words[1:] + words[:1] + suffix
    def tidy(word):
        core = word.strip(".").lower()
        if core in ("ii", "iii", "iv", "v"):
            return word.upper()
        return word.capitalize() if core in SUFFIXES else word
    return " ".join(tidy(w) for w in words)


def is_entity(name):
    """A fund, trust or company rather than a person ('667, L.P.', 'Baker Bros. Advisors (GP) LLC')."""
    return bool(ENTITY.search(name or "")) or bool(re.match(r"^\d", (name or "").strip()))


def company_display_name(name):
    """'MONOLITHIC POWER SYSTEMS, INC.' -> 'Monolithic Power Systems, Inc.' (mixed case is kept as filed)."""
    name = " ".join((name or "").split())
    if name.isupper():
        name = clean_name(name)
    return name or None


def clean_title(title):
    title = " ".join((title or "").split()).strip(" ,;")
    if title.isupper() and len(title) > 4:
        title = title.title().replace("Ceo", "CEO").replace("Cfo", "CFO").replace("Coo", "COO").replace("Cto", "CTO") \
                     .replace("Evp", "EVP").replace("Svp", "SVP").replace("Vp", "VP").replace("And", "and").replace("Of", "of")
    return title


def accession_url(accession, cik):
    """The filing's folder on EDGAR."""
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/" if cik else None
