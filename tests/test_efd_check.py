"""
The eFD access check (python -m insider_tracking check efd) against a stand-in eFD, in every
situation the check knows: refusals, outages, a changed site, missing settings.
"""
import json
import sys
import tempfile
from pathlib import Path

from insider_tracking.checks.efd import EfdCheck
from insider_tracking.checks.framework import PACING, CheckRunner
from insider_tracking.runs.platform import Platform
from insider_tracking.sources.senate.site import EfdEndpoints

from .sites import efd_check as site
from .support.checks import Checks
from .support.commands import run as run_command

BASE = site.start()
EMAIL = "tester@example.com"
check = Checks()


def run(mode, args=("--email", EMAIL), env=None, base_url=None):
    site.MODE = mode
    site.LOG.clear()
    site.CALLS.clear()
    outdir = Path(tempfile.mkdtemp())
    efd = EfdCheck(EfdEndpoints(base_url or BASE), PACING.without_waiting())
    platform = Platform(environ=dict(env or {}), output_dir=outdir, sleep=lambda seconds: None)
    result = run_command(CheckRunner(efd), args, platform)
    summary = json.loads((outdir / "summary.json").read_text(encoding="utf-8"))
    files = sorted(p.name for p in outdir.iterdir())
    leaked = [p.name for p in outdir.iterdir() if EMAIL in p.read_text(encoding="utf-8", errors="replace")]
    return result.code, result.out, summary, files, outdir, leaked


scenarios = [
    # mode, expected exit code, expected verdict, failing step (None = no failure), extra check
    ("ok", 0, "OK", None),
    ("flaky", 0, "OK", None),
    ("blocked", 1, "BLOCKED", 1),
    ("ratelimit", 1, "BLOCKED", 1),
    ("down", 1, "SITE_DOWN", 1),
    ("maintenance", 1, "SITE_DOWN", 1),
    ("notoken", 1, "CHANGED", 1),
    ("csrf", 1, "CHANGED", 2),
    ("noagree", 1, "CHANGED", 2),
    ("sessionlost", 1, "CHANGED", 3),
    ("notjson", 1, "CHANGED", 3),
    ("badrows", 1, "CHANGED", 3),
    ("empty", 0, "OK", None),
    ("paperonly", 0, "OK", None),
    ("reportlost", 1, "CHANGED", 5),
    ("tablechanged", 1, "CHANGED", 5),
]

outputs = {}
for mode, want_code, want_verdict, want_step in scenarios:
    code, out, summary, files, outdir, leaked = run(mode)
    outputs[mode] = (out, summary, files, outdir)
    calls = dict(site.CALLS)
    home_calls = calls.get(("GET", "/search/home/"), 0)
    if mode == "down":
        check("[down] home page tried exactly twice", home_calls == 2)
    if mode in ("blocked", "ratelimit"):
        check(f"[{mode}] no retry after a block", home_calls == 1 and len(calls) == 1)
    if mode == "flaky":
        check("[flaky] home + search each retried once",
                        home_calls == 2 and calls.get(("POST", "/search/report/data/")) == 2)
    last_line = out.strip().splitlines()[-1]
    statuses = [s["status"] for s in summary["steps"]]
    print(f"[{mode}] exit={code} files={files}")
    print(f"    {last_line}")
    print(f"    steps={statuses}")
    check("exit code", code == want_code)
    check("verdict", summary["verdict"] == want_verdict and last_line.startswith(f"VERDICT: {want_verdict}"))
    check("verdict line is last and matches summary.json", last_line == summary["verdict_line"])
    check("5 steps recorded", len(summary["steps"]) == 5)
    check("email not saved in output files", not leaked)
    if want_step:
        check("failing step number", f"at step {want_step} " in last_line)
        check("later steps NOT_RUN", all(s == "NOT_RUN" for s in statuses[want_step:]))
        check("problem_page.html saved", "problem_page.html" in files)

# Scenario-specific checks
out, summary, files, outdir = outputs["ok"]
tx = json.loads((outdir / "transactions.json").read_text(encoding="utf-8"))
print("\n[ok] details")
check("4 output files", files == ["report.html", "search_results.json", "summary.json", "transactions.json"])
check("picked newest electronic (John Sample 10/17)", tx["report"]["last_name"] == "Sample")
check("3 transactions", tx["transaction_count"] == 3)
check("th row cell handled", tx["transactions"][1]["transaction_date"] == "10/02/2026")
check("ticker link text", tx["transactions"][0]["ticker"] == "AAPL")
check("asset name with sub-lines", tx["transactions"][1]["asset_name"].startswith("US Treasury Note Rate/Coupon: 4.25%"))
check("html entity decoded", tx["transactions"][2]["asset_name"] == "Microsoft Corp & Co")
check("counts", summary["counts"] == {"reports_found": 63, "reports_downloaded": 4, "electronic": 3,
                                                 "paper": 1, "other": 0, "transactions_parsed": 3})
check("step 2 shows redirect codes", summary["steps"][1]["http_codes"] == [302, 200])
log = site.LOG  # from the last run, so re-run ok to inspect requests
run("ok")
ua = site.LOG[0][3].get("User-Agent", "")
check("User-Agent names project + email", ua.startswith("insider-tracking/0.1") and EMAIL in ua)
agreement = [entry for entry in site.LOG if entry[1] == "POST" and entry[2] == "/search/home/"][0]
check("agreement form fields", agreement[4]["prohibition_agreement"] == "1" and agreement[4]["csrfmiddlewaretoken"] == site.MASKED)
data = [entry for entry in site.LOG if entry[2] == "/search/report/data/"][0]
check("search form fields", data[4]["report_types"] == "[11]" and data[4]["length"] == "25"
                and data[4]["filer_types"] == "[]")
print("    search form sent:", data[4])
check("search Referer", data[3].get("Referer") == BASE + "/search/")
check("request count = 5", len(site.LOG) == 5)

out, summary, files, outdir = outputs["blocked"]
check("[blocked] Akamai reference captured", summary["akamai_reference"] == "Reference #18.5f3a2d17.1761050000.2b9c4e1")
out, summary, files, outdir = outputs["down"]
check("[down] retried once (2 attempts)", "also after one retry" in summary["steps"][0]["detail"])
out, summary, files, outdir = outputs["paperonly"]
check("[paperonly] steps 4-5 SKIPPED", [s["status"] for s in summary["steps"]][3:] == ["SKIPPED", "SKIPPED"])

# Network failure: nothing listening on port 1
code, out, summary, files, outdir, leaked = run("ok", base_url="http://127.0.0.1:1")
print("\n[no connection]", out.strip().splitlines()[-1])
check("[no connection] SITE_DOWN", summary["verdict"] == "SITE_DOWN" and code == 1)

# No email at all
code, out, summary, files, outdir, leaked = run("ok", args=())
print("\n[no email]", out.strip().splitlines()[-1])
check("[no email] NOT_RUN, nothing sent", summary["verdict"] == "NOT_RUN" and code == 1 and not site.LOG)

# GitHub Actions: email from the secret, no --email
code, out, summary, files, outdir, leaked = run("ok", args=(), env={"GITHUB_ACTIONS": "true", "EFD_CONTACT_EMAIL": EMAIL})
print("\n[github]", out.strip().splitlines()[-1])
check("[github] OK and ran_on GitHub Actions", code == 0 and summary["ran_on"] == "GitHub Actions")
code, out, summary, files, outdir, leaked = run("ok", args=(), env={"GITHUB_ACTIONS": "true", "EFD_CONTACT_EMAIL": ""})
check("[github, secret missing] NOT_RUN with secret advice", summary["verdict"] == "NOT_RUN" and "EFD_CONTACT_EMAIL" in out)

for mode in ("ok", "blocked", "csrf", "paperonly"):
    print(f"\n========== full output: {mode} ==========")
    print(outputs[mode][0])
sys.exit(check.finish())
