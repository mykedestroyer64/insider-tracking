"""
netherlands.py - can a script read Dutch executives' trades (the AFM's MAR 19 register)?

The Dutch regulator, the AFM, keeps a public register of managers' transactions (Market Abuse
Regulation, Article 19) and links to an export of the whole register as a CSV file:

  1. read the site's robots.txt (the rules it gives to scripts)
  2. open the register page
  3. download the CSV export and read it

Three requests in all. Results are saved in "output/netherlands".
"""

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, replace
from urllib import robotparser

from ..core.errors import SiteStop
from ..core.status import BLOCKED, CHANGED, OK
from ..web.checks import BlockPageCheck, unexpected
from ..web.describe import http_codes
from .framework import PACING, Check, CheckDefinition, CheckRunner, common_summary, script_fields, shown, times

SAMPLE_ROWS = 300
BYTES_PER_ROW = 300

DEFINITION = CheckDefinition(
    title="Netherlands (AFM) access check",
    description="Check whether this computer can read Dutch executives' trades from the AFM register.",
    steps=("Read the site's rules for scripts", "Open the register page", "Download the CSV export"),
    site="the AFM",
    folder="netherlands",
    advice={
        "BLOCKED": ["The AFM's site refused the script, or its robots.txt rules it out. Do not try to",
                    "get around this. Stop and share this result."],
        "SITE_DOWN": ["The site had a server error. Try again later."],
        "CHANGED": ["A page or file did not look the way the script expects. Keep the output folder",
                    "and share it."],
    },
)


@dataclass(frozen=True)
class AfmEndpoints:
    base: str = "https://www.afm.nl"

    @property
    def robots(self):
        return self.base + "/robots.txt"

    @property
    def register_page(self):
        return self.base + "/en/sector/registers/meldingenregisters/transacties-leidinggevenden-mar19-"

    @property
    def export(self):
        return self.base + "/export.aspx?type=0ee836dc-5520-459d-bcf4-a4a689de6614&format=csv"


def decode(content):
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    if len(content) >= 2 and content[1:2] == b"\x00":
        return content.decode("utf-16-le")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def describe(header, rows):
    def column(*words):
        for name in header:
            if all(w in name.lower() for w in words):
                return name
        return None
    out = {"columns": header, "rows": len(rows)}
    for label, words in {"date": ("dat",), "type": ("type",), "category": ("categor",),
                         "instrument": ("instrument",), "position": ("positi",), "venue": ("plaats",),
                         "venue_en": ("place",), "status": ("status",), "currency": ("unit",)}.items():
        name = column(*words)
        if name:
            values = Counter(r.get(name, "") for r in rows)
            out[f"{label}_column"] = name
            out[f"{label}_values"] = dict(values.most_common(12)) if label != "date" else None
            if label == "date":
                dates = sorted(v for v in values if re.match(r"\d", v))
                out["date_first_last"] = [dates[0] if dates else None, dates[-1] if dates else None]
    out["examples"] = rows[:3]
    return out


class NetherlandsCheck(Check):
    def __init__(self, endpoints=AfmEndpoints(), pacing=PACING):
        self.endpoints = endpoints
        self.definition = replace(DEFINITION, pacing=pacing)
        self.check = BlockPageCheck(name_the_page=False)

    def clear(self, folder):
        for old in list(folder.glob("*.json")) + list(folder.glob("*.csv")) + list(folder.glob("*.txt")):
            old.unlink()

    def run_steps(self, run, client):
        log, info = run.log, run.info

        log.begin(1)
        response = client.get(self.endpoints.robots, check=self.check, ok_codes=(404,))
        codes = http_codes(response)
        if response.status_code == 404:
            note = "no robots.txt (no rules given)"
        else:
            (run.folder / "robots.txt").write_text(response.text, encoding="utf-8")
            parser = robotparser.RobotFileParser()
            parser.parse(response.text.splitlines())
            if not (parser.can_fetch("*", self.endpoints.register_page) and parser.can_fetch("*", self.endpoints.export)):
                raise SiteStop(BLOCKED, "robots.txt says scripts may not read the register "
                                        "or its export (saved as robots.txt)", response)
            delay = parser.crawl_delay("*")
            note = "robots.txt allows the register" + (f", asks for {delay} s between requests" if delay else "")
        info["robots"] = note
        log.end(OK, note, codes)

        log.begin(2)
        response = client.get(self.endpoints.register_page, check=self.check)
        if "export.aspx" not in response.text:
            raise unexpected(response, "the register page has no export link any more")
        match = re.search(r"([\d.,]+)\s+(?:results|resultaten)", response.text, re.I)
        total = match.group(1) if match else None
        info["notifications_on_page"] = total
        log.end(OK, f"{total} notifications listed" if total else "register page loads", http_codes(response))

        log.begin(3)
        response = client.get(self.endpoints.export, check=self.check)
        kind = response.headers.get("Content-Type", "")
        text = decode(response.content)
        if "<html" in text[:3000].lower():
            raise unexpected(response, "the export came back as a web page, not a data file")
        (run.folder / "export_head.txt").write_text(text[:200_000], encoding="utf-8")
        first_line = text.splitlines()[0] if text else ""
        delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        header = [c.strip() for c in next(reader)]
        while header and not header[-1]:
            header.pop()
        rows = [dict(zip(header, [v.strip() for v in values])) for values in reader if any(v.strip() for v in values)]
        if not rows:
            raise SiteStop(CHANGED, "the export has a header but no rows", response)
        size = len(response.content)
        info.update(content_type=kind, delimiter=delimiter, export_bytes=size, **describe(header, rows))
        with open(run.folder / "export_examples.csv", "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows[:SAMPLE_ROWS])
        info["storage_estimate_mb"] = round(len(rows) * BYTES_PER_ROW / 1e6, 1)
        log.end(OK, f"{len(rows):,} rows ({size / 1e6:.1f} MB, {len(header)} columns: "
                    f"{', '.join(header[:6])}...)", http_codes(response))

    def outcome(self, run):
        info = run.info
        first_last = info.get("date_first_last") or [None, None]
        return (f"- {info['rows']:,} rows in the export, dates {first_last[0]} to {first_last[1]}; "
                f"about {info['storage_estimate_mb']} MB in all")

    def summary(self, run, verdict_line):
        return {**common_summary(run, verdict_line), **times(run), "steps": run.log.steps, **run.info,
                **script_fields()}

    def saved_line(self, run):
        return f"Saved in {shown(run.folder)}: summary.json, export_examples.csv, export_head.txt, robots.txt"


def command():
    """`python -m insider_tracking check netherlands`."""
    return CheckRunner(NetherlandsCheck())

