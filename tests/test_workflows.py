"""
The GitHub workflows (.github/workflows): their names, schedules, settings and secrets, and
the shell steps of the shared collector run, run with bash the way GitHub runs them (with a
stand-in "python" that prints a collector's output), including the weekly heartbeat in a real
git repository.
"""
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import yaml

from insider_tracking.cli import COMMANDS

from .support.checks import Checks

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
check = Checks()


def load(name):
    workflow = yaml.safe_load((WORKFLOWS / name).read_text())
    workflow["on"] = workflow.pop(True, workflow.get("on"))     # YAML 1.1 reads the key "on" as True
    return workflow


def bash(script, cwd, env):
    """Run a step's script the way GitHub runs `shell: bash` (bash -eo pipefail)."""
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                          cwd=cwd, env=env, capture_output=True, text=True)


# ------------------------------------------------------------------ names, schedules, settings
check.section("every workflow")
names = {path.name: load(path.name)["name"] for path in sorted(WORKFLOWS.glob("*.yml"))}
print(names)
check("every file reads as YAML and has a name", all(names.values()))
for path in sorted(WORKFLOWS.glob("*.yml")):
    for line in path.read_text().splitlines():
        if line.strip().startswith("#"):
            continue
        if "python" in line and "-m insider_tracking" in line:
            words = line.split("-m insider_tracking", 1)[1].split()
            command = " ".join(words[:2]).strip('"')
            if "$" not in command:
                check(f"{path.name}: '{command}' is a command", command in COMMANDS, line.strip())

COLLECTORS = {   # file: (workflow name, collector, cron, concurrency group, permission, timeout, artifact, secrets)
    "collect.yml": ("insider-tracking collector", "senate", "23 5,17 * * *", "collector", "write", 30,
                    "collector-output", {"EFD_CONTACT_EMAIL", "SUPABASE_DB_URL", "SUPABASE_DB_PASSWORD"}),
    "collect-house.yml": ("insider-tracking House collector", "house", "53 */3 * * *", "house-collector", "read", 30,
                          "house-collector-output", {"EFD_CONTACT_EMAIL", "SUPABASE_DB_URL", "SUPABASE_DB_PASSWORD"}),
    "collect-sec.yml": ("insider-tracking SEC collector", "sec", "38 */3 * * *", "sec-collector", "read", 40,
                        "sec-collector-output", {"EFD_CONTACT_EMAIL", "SUPABASE_DB_URL", "SUPABASE_DB_PASSWORD"}),
    "collect-sweden.yml": ("insider-tracking Sweden collector", "sweden", "8 */3 * * *", "sweden-collector", "read", 20,
                           "sweden-collector-output", {"EFD_CONTACT_EMAIL", "SUPABASE_DB_URL", "SUPABASE_DB_PASSWORD"}),
    "collect-returns.yml": ("insider-tracking returns collector", "returns", None, "returns-collector", "read", 120,
                            None, {"SHARADAR_API_KEY", "SUPABASE_DB_URL", "SUPABASE_DB_PASSWORD"}),
}
shared = load("_collector.yml")
inputs = shared["on"]["workflow_call"]["inputs"]
declared_secrets = shared["on"]["workflow_call"]["secrets"]
check.section("the collector workflows")
for file, (name, collector, cron, group, permission, timeout, artifact, secrets) in COLLECTORS.items():
    workflow = load(file)
    job = workflow["jobs"]["collect"]
    given = job["with"]
    schedule = [entry["cron"] for entry in workflow["on"].get("schedule") or []]
    check(f"{file}: named '{name}'", workflow["name"] == name, workflow["name"])
    check(f"{file}: schedule {cron or 'none (by hand only)'}, and the Run workflow button",
          schedule == ([cron] if cron else []) and "workflow_dispatch" in workflow["on"], schedule)
    check(f"{file}: one run at a time ({group})", workflow["concurrency"] == {"group": group, "cancel-in-progress": False},
          workflow["concurrency"])
    check(f"{file}: reads the repository; may write to it only where needed ({permission})",
          workflow["permissions"] == {"contents": "read"}
          and job.get("permissions", {"contents": "read"}) == {"contents": permission},
          (workflow["permissions"], job.get("permissions")))
    check(f"{file}: runs the shared collector run", job["uses"] == "./.github/workflows/_collector.yml", job["uses"])
    check(f"{file}: collect {collector}, title, timeout {timeout}, artifact {artifact}",
          given["collector"] == collector and f"collect {collector}" in COMMANDS and given["title"]
          and given["timeout-minutes"] == timeout and given.get("artifact") == artifact, given)
    check(f"{file}: only known settings", set(given) <= set(inputs), set(given) - set(inputs))
    check(f"{file}: every required setting given", {k for k, v in inputs.items() if v.get("required")} <= set(given))
    check(f"{file}: passes exactly the secrets it needs", set(job["secrets"]) == secrets, set(job["secrets"]))
    check(f"{file}: the secrets are known to the shared run", set(job["secrets"]) <= set(declared_secrets))
senate = load("collect.yml")["jobs"]["collect"]["with"]
check("only the Senate collector saves the heartbeat and doesn't count refusals",
      senate.get("heartbeat") is True and senate.get("blocked-note")
      and all("heartbeat" not in load(f)["jobs"]["collect"]["with"] for f in COLLECTORS if f != "collect.yml"))
check("the returns collector posts its requests line as a note, not History",
      "^Requests" in load("collect-returns.yml")["jobs"]["collect"]["with"]["notes"])

check.section("the website workflow")
site = load("site.yml")
check("rebuilds after every collector workflow (by their names)",
      sorted(site["on"]["workflow_run"]["workflows"]) == sorted(entry[0] for entry in COLLECTORS.values()),
      site["on"]["workflow_run"]["workflows"])
check("rebuilds when the code or the site's files change on main",
      site["on"]["push"]["branches"] == ["main"] and {"site/**", "insider_tracking/**"} <= set(site["on"]["push"]["paths"]))
check("after a collector run, only when it succeeded",
      "workflow_run.conclusion == 'success'" in site["jobs"]["build"]["if"])

check.section("the checks workflow")
checks_workflow = load("new-sources-check.yml")


def steps_started(sources):
    """The steps a "Run workflow" with these check names starts (as GitHub reads their if:)."""
    started = []
    for step in checks_workflow["jobs"]["check"]["steps"]:
        match = re.fullmatch(r"contains\(format\(' \{0\} ', inputs\.sources\), ' (\w+) '\)", step.get("if", ""))
        if match and f" {match.group(1)} " in f" {sources} ":
            started.append(match.group(1))
    return started


check("each check is started by its own name only (secdays doesn't start the SEC check)",
      steps_started("libs secdays dbsize sharadar") == ["libs", "secdays", "dbsize", "sharadar"]
      and steps_started("house sec sweden netherlands") == ["house", "sec", "sweden", "netherlands"],
      steps_started("libs secdays dbsize sharadar"))

check.section("the tests workflow")
tests_workflow = load("tests.yml")
tests_job = tests_workflow["jobs"]["tests"]
check("runs on every push and pull request", "push" in tests_workflow["on"] and "pull_request" in tests_workflow["on"])
check("with a PostgreSQL server", tests_job["services"]["postgres"]["image"].startswith("postgres:"))
check("runs every test file", any("python -u -m tests.run" in step.get("run", "") for step in tests_job["steps"]))

# ------------------------------------------------------------------ the shared run's shell steps
steps = {step["name"]: step for step in shared["jobs"]["collect"]["steps"]}
run_step = next(step for name, step in steps.items() if name.startswith("Run the "))
summary_step = steps["Write the verdict to the job summary"]
notes_step = steps["Post the verdict as a note (readable through GitHub's API)"]
heartbeat_step = steps["Weekly heartbeat (stops GitHub from pausing the schedule)"]
check.section("the shared run's steps")
check("summary and notes always run; the output kept only on failure; heartbeat only when asked",
      summary_step["if"] == "always()" and notes_step["if"] == "always()"
      and steps["Keep the output when something went wrong"]["if"].startswith("failure()")
      and heartbeat_step["if"] == "always() && inputs.heartbeat")
check("no ${{ }} inside the scripts (settings come in through env)",
      all("${{" not in step.get("run", "") for step in steps.values()))

work = Path(tempfile.mkdtemp())
stand_in = work / "bin"
stand_in.mkdir()
(stand_in / "python").write_text('#!/bin/bash\ncat "$CONSOLE"\nexit "$EXIT"\n')
(stand_in / "python").chmod((stand_in / "python").stat().st_mode | stat.S_IEXEC)
OK_OUTPUT = ("insider-tracking collector 0.2\n[1/5] Connect to the database ... OK\n      25 of 146 done\n"
             "[5/5] Read reports ... OK - read 146 reports\n\nDatabase now: 159 reports\nHistory: listed back to 09/2024\n"
             "Requests to eFD in this run: 173\nVERDICT: OK - 159 new reports (ran on GitHub Actions)\n")
BLOCKED_OUTPUT = "[1/5] Connect to the database ... OK\n[2/5] Open eFD ... BLOCKED\nVERDICT: BLOCKED at step 2 (ran on GitHub Actions)\n"


def collector_step(output, code, blocked_note=""):
    (work / "console.txt").write_text(output)
    env = dict(os.environ, PATH=f"{stand_in}:{os.environ['PATH']}", CONSOLE=str(work / "console.txt"), EXIT=str(code),
               COLLECTOR="senate", TITLE="Senate collector", BLOCKED_NOTE=blocked_note)
    return bash(run_step["run"], work, env)


result = collector_step(OK_OUTPUT, 0)
check("a run that works: the step passes and the output is kept", result.returncode == 0
      and (work / "output" / "console.log").read_text() == OK_OUTPUT, result.stderr)
result = collector_step(BLOCKED_OUTPUT, 1, blocked_note="eFD refused again; recorded")
check("a refusal with blocked-note: the step passes, with a warning", result.returncode == 0
      and "::warning title=Senate collector::eFD refused again; recorded" in result.stdout, result.stdout + result.stderr)
result = collector_step(BLOCKED_OUTPUT, 1)
check("a refusal without blocked-note: the step fails (GitHub emails)", result.returncode == 1)
result = collector_step(OK_OUTPUT.replace("VERDICT: OK", "VERDICT: ERROR"), 1, blocked_note="x")
check("any other failure still fails the step", result.returncode == 1)

summary = work / "summary.md"
env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary), TITLE="Senate collector", WORKFLOW="insider-tracking collector")
(work / "output" / "console.log").write_text(OK_OUTPUT)
result = bash(summary_step["run"], work, env)
text = summary.read_text()
print("--- job summary ---\n" + text)
check("the summary step works", result.returncode == 0, result.stderr)
check("the summary has the workflow's name, the verdict, the steps and the totals, no progress lines",
      text.startswith("## insider-tracking collector") and "**VERDICT: OK" in text and "[5/5]" in text
      and "Database now" in text and "Requests to eFD" in text and "25 of 146" not in text, text)
(work / "output" / "console.log").write_text("Nothing was done...\nVERDICT: NOT_RUN - settings missing (ran on GitHub Actions)\n")
summary.unlink()
bash(summary_step["run"], work, env)
check("a NOT_RUN summary has no empty code block", "```" not in summary.read_text(), summary.read_text())
(work / "output" / "console.log").write_text("Traceback (most recent call last):\n")
summary.unlink()
bash(summary_step["run"], work, env)
check("no verdict: the summary says which step to open", 'Open the "Run the Senate collector" step' in summary.read_text(),
      summary.read_text())

(work / "output" / "console.log").write_text(OK_OUTPUT)
for notes, wanted, unwanted in ((inputs["notes"]["default"], "History:", "Requests"),
                                (load("collect-returns.yml")["jobs"]["collect"]["with"]["notes"], "Requests", "History:")):
    result = bash(notes_step["run"], work, dict(os.environ, TITLE="Senate collector", NOTES=notes))
    lines = result.stdout.splitlines()
    check(f"notes ({notes}): titled, with {wanted}, without {unwanted} or progress lines",
          lines and all(line.startswith("::notice title=Senate collector::") for line in lines)
          and any(wanted in line for line in lines) and not any(unwanted in line for line in lines)
          and not any("25 of 146" in line for line in lines), lines)

# ------------------------------------------------------------------ the weekly heartbeat
check.section("the weekly heartbeat, in a real git repository")
root = Path(tempfile.mkdtemp())
remote, clone = root / "remote.git", root / "clone"
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True, capture_output=True)
subprocess.run("git -c user.name=t -c user.email=t@t commit -q --allow-empty -m init && git push -q origin HEAD:main",
               shell=True, cwd=clone, check=True)
env = dict(os.environ, GITHUB_REF_NAME="main")
heartbeat = heartbeat_step["run"]


def remote_log():
    return subprocess.run(["git", "--git-dir", str(remote), "log", "--format=%s", "main"],
                          capture_output=True, text=True).stdout.split("\n")


result = bash(heartbeat, clone, env)
check("the first run saves the heartbeat and pushes",
      result.returncode == 0 and "Saved" in result.stdout and remote_log()[0].startswith("Weekly heartbeat"),
      result.stdout + result.stderr)
result = bash(heartbeat, clone, env)
check("a second run the same week does nothing", result.returncode == 0 and "recent enough" in result.stdout
      and remote_log().count("Weekly heartbeat: the collector is running") == 1, result.stdout + result.stderr)
(clone / "status" / "heartbeat.txt").write_text((date.today() - timedelta(days=8)).isoformat() + "\n")
subprocess.run("git -c user.name=t -c user.email=t@t commit -qam old && git push -q origin HEAD:main",
               shell=True, cwd=clone, check=True)
other = root / "other"          # someone else pushes meanwhile: the heartbeat must rebase, not fail
subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True, capture_output=True)
(other / "notes.txt").write_text("uploaded on the website\n")
subprocess.run("git add notes.txt && git -c user.name=o -c user.email=o@o commit -qm upload && git push -q origin HEAD:main",
               shell=True, cwd=other, check=True)
result = bash(heartbeat, clone, env)
check("an 8-day-old heartbeat is renewed even after someone else pushed",
      result.returncode == 0 and remote_log()[0].startswith("Weekly heartbeat"), result.stdout + result.stderr)
(clone / "status" / "heartbeat.txt").write_text("not a date\n")
subprocess.run("git -c user.name=t -c user.email=t@t commit -qam junk && git push -q origin HEAD:main",
               shell=True, cwd=clone, check=True)
result = bash(heartbeat, clone, env)
check("a damaged heartbeat file is simply rewritten", result.returncode == 0 and "Saved" in result.stdout,
      result.stdout + result.stderr)
sys.exit(check.finish())
