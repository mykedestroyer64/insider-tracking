"""
netherlands_check.py - access test for Dutch executives' trades (the AFM's MAR 19 register).

The Dutch regulator, the AFM, keeps a public register of managers' transactions
(Market Abuse Regulation, Article 19) and links to an export of the whole
register as a CSV file. This script tries that:

  1. read the site's robots.txt (the rules it gives to scripts)
  2. open the register page
  3. download the CSV export and read it

Each step gets one status, as in efd_check.py: OK, BLOCKED, SITE_DOWN or
CHANGED (NOT_RUN for steps that never ran). Three requests in all, 2-3 seconds
apart, with a User-Agent that names the project and gives a contact email.

Run it (with your own email):
    python netherlands_check.py --email you@example.com
On GitHub Actions the email comes from the EFD_CONTACT_EMAIL secret instead.

Results are saved in the "output/netherlands" folder. The last line printed is
the verdict. Exit code: 0 if OK, 1 otherwise.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser

import efd_check
from efd_check import CHANGED, NOT_RUN, OK, StepFailed, check_response, http_codes, short_error


PROJECT = "insider-tracking"
VERSION = "0.1"

SITE = "https://www.afm.nl"
ROBOTS = SITE + "/robots.txt"
REGISTER_PAGE = SITE + "/en/sector/registers/meldingenregisters/transacties-leidinggevenden-mar19-"
EXPORT = SITE + "/export.aspx?type=0ee836dc-5520-459d-bcf4-a4a689de6614&format=csv"

SAMPLE_ROWS = 300
BYTES_PER_ROW = 300
OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "netherlands"

STEP_NAMES = {
    1: "Read the site's rules for scripts",
    2: "Open the register page",
    3: "Download the CSV export",
}
TOTAL_STEPS = len(STEP_NAMES)


class StepLog:
    def __init__(self):
        self.steps = []
        self.current = 0

    def begin(self, number):
        self.current = number
        label = f"{STEP_NAMES[number]} ".ljust(40, ".")
        print(f"[{number}/{TOTAL_STEPS}] {label}", end=" ", flush=True)

    def end(self, status, detail="", codes=()):
        codes = list(codes)
        http = f" (HTTP {' -> '.join(str(code) for code in codes)})" if codes else ""
        print(f"{status}{http}" + (f" - {detail}" if detail else ""), flush=True)
        self.steps.append({"step": self.current, "name": STEP_NAMES[self.current],
                           "status": status, "http_codes": codes, "detail": detail})

    def mark_rest(self, status, detail=""):
        for number in range(self.current + 1, TOTAL_STEPS + 1):
            self.begin(number)
            self.end(status, detail)


def shown(path):
    try:
        return str(path.relative_to(Path(__file__).resolve().parent))
    except ValueError:
        return str(path)


def save_json(filename, data):
    with open(OUTPUT_DIR / filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def decode(content):
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    if len(content) >= 2 and content[1:2] == b"\x00":
        return content.decode("utf-16-le")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def step1_robots(web):
    response = web.request("GET", ROBOTS)
    codes = http_codes(response)
    if response.status_code == 404:
        return "no robots.txt (no rules given)", codes
    check_response(response)
    (OUTPUT_DIR / "robots.txt").write_text(response.text, encoding="utf-8")
    parser = robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    if not (parser.can_fetch("*", REGISTER_PAGE) and parser.can_fetch("*", EXPORT)):
        raise StepFailed(efd_check.BLOCKED, "robots.txt says scripts may not read the register "
                                            "or its export (saved as robots.txt)", response)
    delay = parser.crawl_delay("*")
    return f"robots.txt allows the register" + (f", asks for {delay} s between requests" if delay else ""), codes


def step2_register(web):
    response = web.request("GET", REGISTER_PAGE)
    check_response(response)
    text = response.text
    if "export.aspx" not in text:
        raise efd_check.unexpected(response, "the register page has no export link any more")
    match = re.search(r"([\d.,]+)\s+(?:results|resultaten)", text, re.I)
    total = match.group(1) if match else None
    return total, http_codes(response)


def step3_export(web):
    response = web.request("GET", EXPORT)
    check_response(response)
    kind = response.headers.get("Content-Type", "")
    text = decode(response.content)
    if "<html" in text[:3000].lower():
        raise efd_check.unexpected(response, "the export came back as a web page, not a data file")
    (OUTPUT_DIR / "export_head.txt").write_text(text[:200_000], encoding="utf-8")
    first_line = text.splitlines()[0] if text else ""
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = [c.strip() for c in next(reader)]
    while header and not header[-1]:
        header.pop()
    rows = [dict(zip(header, [v.strip() for v in values])) for values in reader if any(v.strip() for v in values)]
    if not rows:
        raise StepFailed(CHANGED, "the export has a header but no rows", response)
    return header, rows, kind, delimiter, len(response.content), http_codes(response)


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


ADVICE = {
    "BLOCKED": ["The AFM's site refused the script, or its robots.txt rules it out. Do not try to",
                "get around this. Stop and share this result."],
    "SITE_DOWN": ["The site had a server error. Try again later."],
    "CHANGED": ["A page or file did not look the way the script expects. Keep the output folder",
                "and share it."],
}


def main():
    parser = argparse.ArgumentParser(description="Check whether this computer can read Dutch "
                                                 "executives' trades from the AFM register.")
    parser.add_argument("--email", help="your contact email, sent in the User-Agent")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    ran_on = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "your computer"
    started = datetime.now(timezone.utc)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in list(OUTPUT_DIR.glob("*.json")) + list(OUTPUT_DIR.glob("*.csv")) + list(OUTPUT_DIR.glob("*.txt")):
        old.unlink()

    print(f"Netherlands (AFM) access check - {PROJECT} {VERSION}")
    print(f"Running on: {ran_on}")

    log = StepLog()
    info, failure, failed_step = {}, None, None
    email = (args.email or os.environ.get("EFD_CONTACT_EMAIL") or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        print("\nNo valid contact email was given, so nothing was sent to the AFM.")
        status, outcome = NOT_RUN, "- no contact email given"
    else:
        print("Contact email: set (it goes in the User-Agent and is not saved in output)\n")
        web = efd_check.PoliteSession(f"{PROJECT}/{VERSION} (non-commercial public transparency "
                                      f"project; contact: {email})")
        try:
            log.begin(1)
            note, codes = step1_robots(web)
            info["robots"] = note
            log.end(OK, note, codes)

            log.begin(2)
            total, codes = step2_register(web)
            info["notifications_on_page"] = total
            log.end(OK, f"{total} notifications listed" if total else "register page loads", codes)

            log.begin(3)
            header, rows, kind, delimiter, size, codes = step3_export(web)
            info.update(content_type=kind, delimiter=delimiter, export_bytes=size, **describe(header, rows))
            with open(OUTPUT_DIR / "export_examples.csv", "w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=header, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows[:SAMPLE_ROWS])
            info["storage_estimate_mb"] = round(len(rows) * BYTES_PER_ROW / 1e6, 1)
            log.end(OK, f"{len(rows):,} rows ({size / 1e6:.1f} MB, {len(header)} columns: "
                        f"{', '.join(header[:6])}...)", codes)
        except StepFailed as error:
            failure, failed_step = error, log.current
            log.end(error.status, error.detail, http_codes(error.response))
            log.mark_rest(NOT_RUN)
            if error.response is not None:
                (OUTPUT_DIR / "problem_page.html").write_bytes(error.response.content[:500_000])
        except Exception as error:
            failure = StepFailed(CHANGED, f"unexpected problem: {short_error(error)}")
            failed_step = log.current
            log.end(CHANGED, failure.detail)
            log.mark_rest(NOT_RUN)
            print("\nTechnical details (useful for fixing the script):")
            traceback.print_exc(file=sys.stdout)

        if failure is not None:
            status = failure.status
            outcome = f"at step {failed_step} ({STEP_NAMES[failed_step]}) - {failure.detail}"
        else:
            status = OK
            first_last = info.get("date_first_last") or [None, None]
            outcome = (f"- {info['rows']:,} rows in the export, dates {first_last[0]} to {first_last[1]}; "
                       f"about {info['storage_estimate_mb']} MB in all")

    verdict_line = f"VERDICT: {status} {outcome} (ran on {ran_on})"
    save_json("summary.json", {
        "verdict": status, "verdict_line": verdict_line, "ran_on": ran_on,
        "started_at_utc": started.isoformat(timespec="seconds"),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": log.steps, **info,
        "script": f"{PROJECT} {VERSION}", "python": sys.version.split()[0],
    })
    print()
    advice = ADVICE.get(status)
    if advice:
        print("\n".join(advice))
    print(f"Saved in {shown(OUTPUT_DIR)}: summary.json, export_examples.csv, export_head.txt, robots.txt")
    print(verdict_line)
    return 0 if status == OK else 1


if __name__ == "__main__":
    sys.exit(main())
